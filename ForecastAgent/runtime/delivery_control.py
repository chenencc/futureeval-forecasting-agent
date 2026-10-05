"""Bounded delivery of a saved read before a soft stall stop.

This opt-in control attests coordinates and version identity, not relevance or
comprehension. It never changes progress counters, receipts, or provider budgets.
"""
import json

from ForecastAgent.readers.saved import select, version_digest
from ForecastAgent.runtime.progress import fingerprint
from ForecastAgent.tavily_research import canonical_url

POLICY_FIELD = 'drain_unseen_reads_before_stall'


def pending_reads(task, *, inspect_only=False):
    """Inspect only the newest complete tool group, which context can deliver."""
    if not inspect_only and task.bundle['request'].get(POLICY_FIELD) is not True:
        return []
    messages = task.bundle.get('messages', [])
    start = next((i for i in range(len(messages)-1, -1, -1)
                  if messages[i].get('role') == 'assistant'), None)
    if start is None:
        return []
    calls = {c['id']: c.get('function', {}) for c in messages[start].get('tool_calls', [])}
    replies = {m.get('tool_call_id'): m for m in messages[start+1:] if m.get('role') == 'tool'}
    if not calls or set(calls) != set(replies):
        return []
    progress = task.bundle.get('progress', {})
    # Legacy execution-as-delivery claims are not confirmed receipts.
    delivered = progress.get('reads', {}) if progress.get('delivery_schema') == 2 else {}
    pending = []
    for ident, call in calls.items():
        if call.get('name') != 'read_document':
            continue
        try:
            args = json.loads(call['arguments'])
            payload = json.loads(replies[ident]['content'])
            data = payload.get('data', payload)
            if payload.get('ok') is False or data.get('error') or data.get('blocked') or data.get('no_progress'):
                continue
            url = canonical_url(args['url'])
            page, body, _ = select(task.bundle['pages'], url, args.get('document_index'))
            left, right = data['start_char'], data['end_char']
            if (type(left) is not int or type(right) is not int or not 0 <= left < right <= len(body)
                    or body[left:right] != data.get('content') or not data['content'].strip()
                    or not data.get('source_sha256') or page.get('sha256') != data['source_sha256']):
                continue
            from ForecastAgent.readers.quality import body_diagnostics
            from ForecastAgent.runtime.collection_v2 import eligible
            if not body_diagnostics(page.get('content', ''))['usable_text'] or task.verified_only and not eligible(page, task.cutoff):
                continue
            scope = fingerprint([url, version_digest(page), 'read_document', args.get('document_index')])
            uncovered = [(left, right)]
            for row in delivered.values():
                if row['scope'] != scope:
                    continue
                uncovered = [(a, b) for l, r in uncovered for a, b in
                             ((l, min(r, row['start'])), (max(l, row['end']), r)) if a < b]
            if uncovered:
                pending.append({'tool_call_id': ident, 'tool': 'read_document', 'url': url,
                    'document_index': args.get('document_index'), 'source_sha256': page['sha256'],
                    'source_parsed_sha256': version_digest(page), 'start_char': left, 'end_char': right,
                    'undelivered_ranges': uncovered, 'scope': 'Delivery pending; relevance and comprehension unverified.'})
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return pending
