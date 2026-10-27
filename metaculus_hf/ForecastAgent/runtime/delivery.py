"""Separate saved tool execution from confirmed model-visible delivery.

No provider-specific behavior or source-specific interpretation belongs here.
Receipts attest successful request inclusion, not model comprehension or truth.
"""
from copy import deepcopy
import json

from ForecastAgent.readers.saved import select


def ensure_delivery_state(task):
    progress = task.bundle.setdefault('progress', {})
    progress.setdefault('visible_reads', {})
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
        if data['next_start'] is not None:
            data['next_document_args'] = None
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
        if 'row_locations' in data:
            data['row_locations'] = data['row_locations'][:len(rows)]
        end = args.get('offset', 0) + len(rows)
        data['next_offset'] = end if end < data.get('total', end) else None
        data['delivery'] = {'visible_row_count': len(rows), 'executed_row_count': original_count,
            'continuation_required': len(rows) != original_count,
            'instruction': 'Only complete visible rows are delivered. Use next_offset for remaining rows. An oversized row is not silently counted as read.'}
    return value


def stage_read_passages(task, data, args):
    """Expose exact visible spans through the existing passage-review protocol."""
    from ForecastAgent.runtime.progress import fingerprint
    from ForecastAgent.runtime.needs import active_needs
    from ForecastAgent.readers.saved import version_digest
    from ForecastAgent.tavily_research import canonical_url
    if not isinstance(data.get('content'), str) or not data['content'].strip():
        return []
    url = canonical_url(args['url'])
    page, text, _ = select(task.bundle['pages'], url, args.get('document_index'))
    start, end = data['start_char'], data['end_char']
    if text[start:end] != data['content']:
        raise ValueError('Cannot stage a passage from an altered source projection')
    needs = [n['id'] for n in active_needs(task.bundle)]
    rows = []
    while start < end:
        right = min(start + 4000, end)
        if right < end:
            boundary = text.rfind('\n', start + 2000, right)
            if boundary >= 0:
                right = boundary + 1
        pid = 'P' + fingerprint([url, version_digest(page), args.get('document_index'), start, right, 'source_read'])
        task.bundle.setdefault('passages', {})[pid] = {
            'url': url, 'document_index': args.get('document_index'), 'start_char': start, 'end_char': right,
            'source_version': version_digest(page), 'source_sha256': page.get('sha256')}
        task.bundle.setdefault('progress', {}).setdefault('delivery_passages', {})[pid] = {
            'need_ids': needs, 'origin': 'exact_source_read',
            'association_warning': 'Available need IDs are choices, not automatic relevance assignments.'}
        rows.append({'passage_id': pid, 'url': url, 'document_index': args.get('document_index'),
            'start_char': start, 'end_char': right})
        start = right
    return rows


def recover_read_passages(task):
    """Re-expose confirmed reads after a restore without HTTP or text rewriting."""
    from ForecastAgent.runtime.progress import fingerprint
    from ForecastAgent.readers.saved import version_digest
    from ForecastAgent.tavily_research import canonical_url
    receipts = task.bundle.get('progress', {}).get('delivery_receipts', {})
    for receipt in list(receipts.values())[-24:]:
        if receipt.get('tool') != 'read_document' or not receipt.get('visible_range'):
            continue
        row = receipt['visible_range']
        try:
            url = canonical_url(row['url'])
            args = {'url': url, 'document_index': (row.get('location') or {}).get('document_index')}
            page, text, _ = select(task.bundle['pages'], url, args['document_index'])
            from ForecastAgent.runtime.collection_v2 import eligible
            from ForecastAgent.readers.quality import body_diagnostics
            if not body_diagnostics(page.get('content', ''))['usable_text'] or task.verified_only and not eligible(page, task.cutoff):
                continue
            scope = fingerprint([url, version_digest(page), 'read_document', args['document_index']])
            confirmed = any(r['scope'] == scope and r['start'] <= row['start_char'] and r['end'] >= row['end_char']
                            for r in task.bundle['progress'].get('reads', {}).values())
            if page.get('sha256') == row.get('source_sha256') and confirmed:
                stage_read_passages(task, {**row, 'content':text[row['start_char']:row['end_char']]}, args)
        except (KeyError, ValueError, TypeError):
            continue


def projected_visibility(task, messages):
    """Derive ephemeral tool availability from the exact proposed request."""
    from ForecastAgent.runtime.progress import fingerprint
    from ForecastAgent.readers.saved import version_digest
    from ForecastAgent.tavily_research import canonical_url
    calls = {c['id']: c.get('function', {}) for m in messages
             if m.get('role') == 'assistant' for c in m.get('tool_calls', [])}
    ranges = {}
    for message in messages:
        call = calls.get(message.get('tool_call_id'))
        if message.get('role') != 'tool' or not call or call.get('name') != 'read_document':
            continue
        try:
            args = json.loads(call['arguments']); payload = json.loads(message['content'])
            data = payload.get('data', payload)
            url = canonical_url(args['url'])
            page, text, _ = select(task.bundle['pages'], url, args.get('document_index'))
            start, end = data['start_char'], data['end_char']
            if text[start:end] != data.get('content') or end <= start:
                continue
            scope = fingerprint([url, version_digest(page), 'read_document', args.get('document_index')])
            ranges[fingerprint([scope, start, end])] = {'scope':scope, 'url':url, 'start':start, 'end':end}
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return ranges


def acknowledge(task, messages):
    """Commit receipts only after a model response to these exact messages."""
    from ForecastAgent.runtime.progress import delivered, fingerprint
    ensure_delivery_state(task)
    calls = {call['id']: call.get('function', {}) for message in messages
             if message.get('role') == 'assistant' for call in message.get('tool_calls', [])}
    attempt = len(task.bundle.get('model_attempts', []))
    receipts = task.bundle['progress'].setdefault('delivery_receipts', {})
    # Working memory is request-local; durable reading coverage is cumulative.
    # An evicted source remains accessible from cache without buying progress.
    task.bundle['progress']['visible_reads'] = {}
    task._projected_visible_reads = None
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
                from ForecastAgent.readers.datasets import read_rows
                expected = read_rows(task.bundle['pages'][canonical_url(args['url'])],args)
                if expected['rows'][:len(data['rows'])] != data['rows']:
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
                    'row_count': len(data['rows']), 'next_offset': data.get('next_offset'),
                    'date_filter':data.get('date_filter')}
                if args.get('need_ids') and data['rows']:
                    from ForecastAgent.readers.saved import version_digest
                    read = {'url':args['url'],'need_ids':args['need_ids'],
                        'source_sha256':task.bundle['pages'][canonical_url(args['url'])].get('sha256'),
                        'source_parsed_sha256':version_digest(task.bundle['pages'][canonical_url(args['url'])]),
                        **receipts[key]['visible_range'],'truth_verified':False,
                        'association_scope':'Agent-selected needs, confirmed exact row delivery; not semantic adequacy.'}
                    task.bundle.setdefault('dataset_reads',{})[fingerprint(read)] = read
        except (KeyError, TypeError, json.JSONDecodeError):
            # Non-reading tool replies may use strings or legacy envelopes.
            continue
