"""Opt-in saved-coverage navigation and literal support for stage labels."""
import copy
import re
from datetime import date, datetime

FIELD = 'research_grounding_policy'
POLICY = 'saved_coverage_stage_v1'
LABEL_POLICY = 'literal_stage_time_isolation_v1'
DATE = re.compile(r'\b(?:\d{4}-\d{2}-\d{2}|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b', re.I)
GUIDE = '''
Saved-coverage discipline: source_catalog describes the whole accessible saved
body, not just the currently delivered excerpt. Dates are lexical navigation,
never event-time or adequacy certification. Before requesting already-saved
material, inspect locally by URL/query or paired start_date/end_date. A limited
view is an unread gap, not a missing source. Preserve genuinely unavailable gaps.
Read source_context alongside a measurement row: title, table header, live status,
nearby section/date heading, revision and qualifier can change its meaning.
Use inspect_research_state limit=1..4 and the exact supplied reference handles,
revision and material hash. Unknown nodes have evidence_ids=[]; record an unread
archive as unreviewed, not not_found or not_yet_published. Another date is a dated baseline,
not the target result. Planned, ongoing or completed observation labels need a
short literal stage_basis in the bound evidence_ids, including a context handle
when needed. A date or a column named Close alone does not prove finality. If the
source or limitation leaves finality disputed, use unknown. The program checks
the quote binding only; the model must reconcile conflicting status information.
'''


def enabled(bundle):
    return bundle.get('request', {}).get(FIELD) == POLICY


def dates(text):
    """Parse only explicit labels. Never infer an event date from a capture clock."""
    result = []
    for match in DATE.finditer(text):
        label = match.group()
        normalized = label.replace(',', '').replace('.', '')
        parsed = None
        for fmt in ('%Y-%m-%d', '%b %d %Y', '%B %d %Y', '%d %B %Y'):
            try:
                parsed = datetime.strptime(normalized, fmt).date().isoformat()
                break
            except ValueError:
                pass
        if parsed:
            result.append((parsed, label))
    return result


def select(material, args):
    """Filter exact catalog spans without changing source coordinates or scope."""
    url = args.get('url')
    if url and url not in material['sources']:
        raise ValueError('Use an exact saved URL from source_catalog')
    start, end = args.get('start_date'), args.get('end_date')
    if bool(start) != bool(end):
        raise ValueError('Provide paired start_date and end_date')
    if start and (date.fromisoformat(start).isoformat() != start or
                  date.fromisoformat(end).isoformat() != end or start > end):
        raise ValueError('Use an ordered ISO date range')
    query = args.get('query', '').casefold()
    return [s for s in material['spans'].values()
            if (not url or s['url'] == url)
            and (not query or query in s['text'].casefold() or query in s['url'].casefold())
            and (not start or any(start <= d <= end for d, _ in dates(s['text'])))]


def inventory(bundle, material, *, offset=0, limit=4, brief=False):
    """Counts and literal date extents are navigation, never recall/reading credit."""
    grouped = {}
    for span in material['spans'].values():
        grouped.setdefault(span['url'], []).append(span)
    rows = []
    for url, spans in grouped.items():
        labels = sorted(set(pair for s in spans for pair in dates(s['text'])))
        page = bundle['pages'][url]
        rows.append({'url': url, 'body_sha256': material['sources'][url]['body_sha256'],
            'saved_chars': len(page['content']), 'accessible_chars': sum(s['end']-s['start'] for s in spans),
            'span_count': len(spans), 'source_truncated': material['sources'][url]['truncated'],
            'lexical_date_count': len({d for d, _ in labels}),
            'lexical_date_start': labels[0][0] if labels else None,
            'lexical_date_end': labels[-1][0] if labels else None,
            'boundary_date_labels': [p[1] for p in (labels[:2]+labels[-2:])],
            'table_line_count': sum(s['text'].lstrip().startswith('|') for s in spans),
            'interpretation_verified': False})
    if brief:
        rows=[{k:v for k,v in row.items() if k not in
            {'boundary_date_labels','table_line_count','interpretation_verified'}} for row in rows]
    end = min(offset+limit, len(rows))
    return {'schema': POLICY, 'sources': rows[offset:end], 'total_sources': len(rows),
        'next_source_offset': end if end < len(rows) else None,
        'scope': 'accessible catalog spans only; restricted visible windows stay restricted',
        'instruction': 'Lexical navigation only; inspect locally before declaring a gap.' if brief else
            'Counts and lexical date ranges are navigation only. Inspect matching original spans before declaring coverage or a missing-data gap.'}


def context(material, selected, maximum=3):
    """Deliver nearby headings and table headers without crossing hidden windows."""
    selected_ids = {s['evidence_id'] for s in selected}
    candidates = []
    for url in dict.fromkeys(s['url'] for s in selected):
        spans = sorted((s for s in material['spans'].values() if s['url'] == url), key=lambda s:s['start'])
        if not spans:
            continue
        for row in (s for s in selected if s['url'] == url):
            windows = material.get('visible_windows')
            lower = next((w['start'] for w in windows if w['url'] == url and
                w['start'] <= row['start'] < row['end'] <= w['end']), row['start']) if windows is not None else 0
            prior = [s for s in spans if lower <= s['start'] and s['end'] <= row['start']]
            opening = next((s for s in spans if s['start'] >= lower), None)
            if opening:
                candidates.append(opening)
            headers = [s for s in prior if row['text'].lstrip().startswith('|') and
                s['text'].lstrip().startswith('|') and
                re.search(r'\|\s*(?:date|year|period|metric|name|country)\s*\|', s['text'], re.I)]
            if headers:
                candidates.append(headers[-1])
            ancestors, heading_chain = [], []
            after_text = False
            for span in prior:
                heading = re.match(r'^\s*(#{1,6})\s+[^\n]+', span['text'])
                if not heading:
                    after_text = bool(span['text'].strip()) or after_text
                    continue
                level = len(heading[1])
                ancestors = [(n, s) for n, s in ancestors if n < level]
                ancestors.append((level, span))
                if after_text:
                    heading_chain = []
                heading_chain.append(span)
                after_text = False
            candidates.extend(reversed(heading_chain))
            candidates.extend(s for _, s in reversed(ancestors))
    result, seen = [], set(selected_ids)
    for span in candidates:
        if span['evidence_id'] not in seen:
            result.append(copy.deepcopy(span)); seen.add(span['evidence_id'])
    return result[:maximum]


def isolate_labels(original, material, allowed=None):
    """Quarantine unsupported annotations without repairing the observation."""
    chosen, errors = isolate_stage(original, material, allowed)
    if chosen.get('kind') != 'observation' or chosen.get('time_status') != 'source_stated':
        return chosen, errors
    phrase = chosen.get('event_time', '')
    refs = [material['spans'].get(i) for i in chosen.get('evidence_ids', [])
            if allowed is None or i in allowed]
    if isinstance(phrase, str) and phrase.strip() and any(s and phrase in s['text'] for s in refs):
        return chosen, errors
    chosen.update(event_time='', time_status='unknown')
    errors.append({'section':'node_labels', 'node_id':chosen.get('id'),
        'field':'event_time', 'proposed':original.get('event_time'),
        'proposed_status':original.get('time_status'), 'accepted':'', 'accepted_status':'unknown',
        'error':'Time annotation has no literal support in its bound inspected spans. Observation retained with unknown timing.'})
    return chosen, errors


def isolate_stage(original, material, allowed=None):
    """Keep a valid observation while quarantining unsupported stage metadata."""
    chosen = copy.deepcopy(original)
    if chosen.get('kind') != 'observation' or chosen.get('event_stage') not in {'planned','ongoing','completed'}:
        return chosen, []
    basis = chosen.get('stage_basis', '')
    refs = [material['spans'].get(i) for i in chosen.get('evidence_ids', [])
            if allowed is None or i in allowed]
    # A date/number-only measurement row has no source statement about phase.
    # This structural check is not an English status classifier or truth judge.
    has_statement = bool(re.search(r'[^\W\d_]', DATE.sub('', basis), re.UNICODE))
    if has_statement and any(s and basis in s['text'] for s in refs):
        return chosen, []
    chosen['event_stage'] = 'unknown'; chosen['stage_basis'] = ''
    return chosen, [{'section':'node_labels', 'node_id':chosen.get('id'),
        'field':'event_stage', 'proposed':original.get('event_stage'), 'accepted':'unknown',
        'error':'Stage label lacks a substantive literal stage_basis in its bound original evidence; date/number-only rows do not establish phase. Observation retained.'}]


def schema(base):
    result = copy.deepcopy(base)
    node = result['properties']['nodes']['items']
    node['required'].append('stage_basis')
    node['properties']['stage_basis'] = {'type':'string','maxLength':180,
        'description':('Short literal source statement in evidence_ids supporting planned/ongoing/completed. Include a source_context handle. Empty for unknown/not_applicable; dates, numeric rows and Close column headings alone cannot establish completed.')}
    node['properties']['evidence_ids']['description'] = 'Observation: 1-3 exact supplied R handles, including a heading when needed. Unknown: empty array []; unread originals are unreviewed gaps, not proof of absence. Never shorten or invent a handle.'
    return result
