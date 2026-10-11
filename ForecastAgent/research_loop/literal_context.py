"""Optional contiguous original context; no entity, date or truth inference."""
import copy
import re

from ForecastAgent.analysis.pilot import digest

FIELD = 'research_literal_context_policy'
POLICY = 'adjacent_original_windows_v1'
RECORD_POLICY = 'record_delimited_original_windows_v2'
MAX_CHARS = 1400
MAX_PARTS = 8


def enabled(bundle):
    policy = bundle.get('request', {}).get(FIELD, 'disabled')
    if policy not in {'disabled', POLICY, RECORD_POLICY}:
        raise ValueError('Unknown literal context policy')
    return policy != 'disabled'


def additions(spans, text, *, policy=POLICY):
    """Add exact windows without replacing existing handles or crossing hidden text.

    Only whitespace gaps between already visible spans may enter a window. This
    also prevents expansion across omitted images and disjoint visible scopes.
    Windows never cross representation or decoded-post provenance boundaries.
    """
    if policy not in {POLICY, RECORD_POLICY}:
        raise ValueError('Unknown literal context partition policy')
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
                      context_policy=policy, meaning_verified=False)
        if provenance(first):
            window['json_provenance'] = copy.deepcopy(provenance(first))
        window['evidence_id'] = 'R' + digest({k: window[k] for k in
            ('url', 'body_sha256', 'start', 'end', 'view_sha256') if k in window})[:24]
        result.append(window)

    ordered = sorted(spans, key=lambda s: (s['start'], s['end']))
    # A whole multiline Markdown link is a syntactic record terminator. No
    # domain, person name, role or relationship is extracted from it. Single
    # inline links and images do not create this kind of boundary.
    boundaries = set()
    if policy == RECORD_POLICY:
        pattern = re.compile(r'^\[(?=[^\]]{1,1400}\]\([^\s()]+\)[ \t]*(?:\r?\n|$))'
                             r'[^\]\n]{1,400}\n[^\]]{1,1400}\]\([^\s()]+\)[ \t]*(?=\r?\n|$)', re.M)
        for match in pattern.finditer(text):
            end_span = next((s for s in ordered if s['start'] < match.end() <= s['end']), None)
            if end_span:
                boundaries.add(end_span['end'])
        if not boundaries:
            return additions(spans, text, policy=POLICY)

    def flush_record(*, mark=True):
        """End at the observed delimiter, preserving bounded leading context."""
        nonlocal group
        # Long introductions must not push the title/name across an arbitrary
        # window boundary. Keep the bounded suffix ending at the real delimiter;
        # earlier text stays selectable in its own exact windows/primitives.
        suffix = len(group)
        while suffix > 1 and (suffix > MAX_PARTS or
                             group[-1]['end']-group[-suffix]['start'] > MAX_CHARS):
            suffix -= 1
        prefix, tail = group[:-suffix], group[-suffix:]
        group = []
        for span in prefix:
            if group and (len(group)>=MAX_PARTS or span['end']-group[0]['start']>MAX_CHARS):
                emit();group=[]
            group.append(span)
        emit()
        group = tail
        emit()
        if mark and result and result[-1]['end'] == tail[-1]['end']:
            result[-1]['record_delimiter'] = 'multiline_markdown_link_end'
            result[-1]['leading_record_context_bounded'] = bool(prefix)
            result[-1]['record_relation_verified'] = False
        group = []

    for span in ordered:
        start, end = span['start'], span['end']
        if not 0 <= start < end <= len(text) or text[start:end] != span['text']:
            raise ValueError('Literal context requires exact current reading coordinates')
        if group:
            previous, first = group[-1], group[0]
            incompatible = (start < previous['end'] or
                text[previous['end']:start].strip() or
                (policy == POLICY and (end-first['start']>MAX_CHARS or len(group)>=MAX_PARTS)) or
                any(previous.get(k) != span.get(k) for k in metadata_keys) or
                provenance(previous) != provenance(span))
            if incompatible:
                if policy == RECORD_POLICY:
                    flush_record(mark=False)
                else:
                    emit()
                group = []
        group.append(span)
        if policy == RECORD_POLICY and end in boundaries:
            flush_record()
    if policy == RECORD_POLICY:
        # The trailing segment has no closing record marker; retain exact text
        # under the ordinary limits without claiming structural completeness.
        if group:
            flush_record(mark=False)
    emit()
    return result
