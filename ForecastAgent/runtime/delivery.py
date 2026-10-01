"""Separate saved tool execution from confirmed model-visible delivery.

No provider-specific behavior or source-specific interpretation belongs here.
Receipts attest successful request inclusion, not model comprehension or truth.
"""
from copy import deepcopy
import json

from ForecastAgent.readers.saved import select


def ensure_delivery_state(task):
    progress = task.bundle.setdefault('progress', {})
    if progress.get('delivery_schema') == 2:
        return
    # Earlier engines marked execution as delivery. Keep those claims for audit,
    # but never use them to deny access to material not demonstrably delivered.
    if progress.get('reads'):
        progress['legacy_unconfirmed_reads'] = deepcopy(progress['reads'])
    progress['reads'] = {}
    progress['delivery_schema'] = 2
    progress.setdefault('delivery_receipts', {})


def project_reply(payload, name, args, text_chars=6000):
    """Page exact read results, preserving original coordinates and opaque keys."""
    value = deepcopy(payload)
    if not isinstance(value, dict):
        return value
    data = value.get('data', value)
    if not isinstance(data, dict) or value.get('ok') is False or data.get('blocked'):
        return value
    if name == 'read_document' and isinstance(data.get('content'), str):
        original_end = data.get('end_char')
        data['content'] = data['content'][:text_chars]
        end = data.get('start_char', 0) + len(data['content'])
        data['end_char'] = end
        data['next_start'] = end if end < data.get('total_chars', end) else None
        data['delivery'] = {'coordinate_space': data.get('location', {}).get('coordinate_space'),
            'visible_start': data.get('start_char', 0), 'visible_end': end,
            'executed_end': original_end, 'continuation_required': original_end != end,
            'instruction': 'Only this exact slice is visible. Use next_start to read the remainder; record useful exact quotes before continuing.'}
    elif name == 'read_dataset_rows' and isinstance(data.get('rows'), list):
        rows = []
        for row in data['rows']:
            if len(json.dumps(rows + [row], ensure_ascii=False)) > text_chars:
                break
            rows.append(row)
        original_count = len(data['rows'])
        data['rows'] = rows
        end = args.get('offset', 0) + len(rows)
        data['next_offset'] = end if end < data.get('total', end) else None
        data['delivery'] = {'visible_row_count': len(rows), 'executed_row_count': original_count,
            'continuation_required': len(rows) != original_count,
            'instruction': 'Only complete visible rows are delivered. Use next_offset for remaining rows. An oversized row is not silently counted as read.'}
    return value


def acknowledge(task, messages):
    """Commit receipts only after a model response to these exact messages."""
    from ForecastAgent.runtime.progress import delivered, fingerprint
    ensure_delivery_state(task)
    calls = {call['id']: call.get('function', {}) for message in messages
             if message.get('role') == 'assistant' for call in message.get('tool_calls', [])}
    attempt = len(task.bundle.get('model_attempts', []))
    receipts = task.bundle['progress'].setdefault('delivery_receipts', {})
    for message in messages:
        call = calls.get(message.get('tool_call_id'))
        if message.get('role') != 'tool' or not call:
            continue
        try:
            args = json.loads(call.get('arguments', '{}'))
            payload = json.loads(message['content'])
            if not isinstance(payload, dict):
                continue
            data = payload.get('data', payload)
            if not isinstance(data, dict):
                continue
            name = call['name']
            if payload.get('ok') is False or data.get('error') or data.get('blocked'):
                continue
            if name == 'read_document':
                _, body, _ = select(task.bundle['pages'], args['url'], args.get('document_index'))
                if body[data['start_char']:data['end_char']] != data.get('content'):
                    raise ValueError('Projected read slice does not match saved source coordinates')
            elif name == 'read_dataset_rows':
                from ForecastAgent.tavily_research import canonical_url
                rows = task.bundle['pages'][canonical_url(args['url'])]['rows']
                start = args.get('offset', 0)
                if rows[start:start + len(data['rows'])] != data['rows']:
                    raise ValueError('Projected dataset rows do not match saved rows')
            delivered(task, name, args, data)
            digest = fingerprint(payload)
            key = fingerprint([attempt, message['tool_call_id'], digest])
            receipts[key] = {'tool_call_id': message['tool_call_id'], 'tool': name,
                'model_attempt_index': attempt, 'projected_payload_sha256': digest,
                'kind': 'successful_request_inclusion', 'comprehension_verified': False}
            if name == 'read_document':
                receipts[key]['visible_range'] = {k: data.get(k) for k in ('url', 'start_char', 'end_char', 'next_start', 'location', 'source_sha256')}
            elif name == 'read_dataset_rows':
                receipts[key]['visible_range'] = {'url': args['url'], 'offset': args.get('offset', 0),
                    'row_count': len(data['rows']), 'next_offset': data.get('next_offset')}
        except (KeyError, TypeError, json.JSONDecodeError):
            # Non-reading tool replies may use strings or legacy envelopes.
            continue
