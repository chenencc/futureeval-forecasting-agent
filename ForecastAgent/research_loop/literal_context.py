"""Optional contiguous original context; no entity, date or truth inference."""
import copy

from ForecastAgent.analysis.pilot import digest

FIELD = 'research_literal_context_policy'
POLICY = 'adjacent_original_windows_v1'
MAX_CHARS = 1400
MAX_PARTS = 8


def enabled(bundle):
    policy = bundle.get('request', {}).get(FIELD, 'disabled')
    if policy not in {'disabled', POLICY}:
        raise ValueError('Unknown literal context policy')
    return policy == POLICY


def additions(spans, text):
    """Add exact windows without replacing existing handles or crossing hidden text.

    Only whitespace gaps between already visible spans may enter a window. This
    also prevents expansion across omitted images and disjoint visible scopes.
    Windows never cross representation or decoded-post provenance boundaries.
    """
    result, group = [], []
    metadata_keys = ('coordinate_space', 'view_sha256', 'parser',
                     'topic_completeness_verified')

    def provenance(span):
        return span.get('json_provenance', [])

    def emit():
        if len(group) < 2:
            return
        first, last = group[0], group[-1]
        start, end = first['start'], last['end']
        if text[start:end] == first['text']:
            return
        window = {k: copy.deepcopy(first[k]) for k in
                  ('source_id', 'url', 'body_sha256', *metadata_keys) if k in first}
        window.update(start=start, end=end, text=text[start:end],
                      reference_kind='literal_context_window',
                      constituent_reference_ids=[s['evidence_id'] for s in group],
                      context_policy=POLICY, meaning_verified=False)
        if provenance(first):
            window['json_provenance'] = copy.deepcopy(provenance(first))
        window['evidence_id'] = 'R' + digest({k: window[k] for k in
            ('url', 'body_sha256', 'start', 'end', 'view_sha256') if k in window})[:24]
        result.append(window)

    for span in sorted(spans, key=lambda s: (s['start'], s['end'])):
        start, end = span['start'], span['end']
        if not 0 <= start < end <= len(text) or text[start:end] != span['text']:
            raise ValueError('Literal context requires exact current reading coordinates')
        if group:
            previous, first = group[-1], group[0]
            incompatible = (start < previous['end'] or
                text[previous['end']:start].strip() or
                end - first['start'] > MAX_CHARS or len(group) >= MAX_PARTS or
                any(previous.get(k) != span.get(k) for k in metadata_keys) or
                provenance(previous) != provenance(span))
            if incompatible:
                emit()
                group = []
        group.append(span)
    emit()
    return result
