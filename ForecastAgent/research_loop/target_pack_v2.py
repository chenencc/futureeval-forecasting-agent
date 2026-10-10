"""Opt-in saved-text selection with local dates, complete rows and source roles.

All scores are navigation heuristics. Dates and authority matches do not certify
event timing, relevance or truth. No HTTP, generated summaries or hidden labels.
"""
import copy
import hashlib
import re
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

from ForecastAgent.acquisition import handoff
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.acquisition.pipeline import reject_outcomes
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.research_loop import target_pack as legacy

PROTOCOL = 'local-context-saved-selection-v2'
MONTHS = ('January February March April May June July August September October November December').split()
MONTH = r'(?:' + '|'.join(m[:3] + r'(?:' + m[3:] + r')?' for m in MONTHS) + r')\.?'
DATE = re.compile(r'\b(?:\d{4}-\d{2}-\d{2}|' + MONTH + r'\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+' + MONTH + r'\s+\d{4})\b', re.I)
NUMBER = re.compile(r'[-+]?\d[\d,]*(?:\.\d+)?\s*(?:%|[BMK])?')


def dates(text):
    """Parse explicit literal dates only; never convert 'today' or capture time."""
    result = []
    for match in DATE.finditer(text):
        literal = match.group()
        cleaned = literal.replace(',', '').replace('.', '')
        for fmt in ('%Y-%m-%d', '%B %d %Y', '%b %d %Y', '%d %B %Y', '%d %b %Y'):
            try:
                result.append({'literal': literal, 'iso': datetime.strptime(cleaned, fmt).date().isoformat()})
                break
            except ValueError:
                continue
    return result


def terms(text):
    # Numeric thresholds and common function words must not rank unrelated rows.
    return {w for w in re.findall(r'[a-z][a-z0-9-]*', legacy.plain(text))
            if len(w) >= 3 and w not in legacy.STOP}


def source_role(url, rule_urls):
    """Literal URL roles: same authority does not imply the same instrument."""
    current = urlparse(url)
    host = (current.hostname or '').removeprefix('www.')
    current_q = parse_qs(current.query)
    authority = False
    for rule in rule_urls:
        wanted = urlparse(rule)
        authority |= host == (wanted.hostname or '').removeprefix('www.')
        if host != (wanted.hostname or '').removeprefix('www.'):
            continue
        if current.path.rstrip('/') != wanted.path.rstrip('/'):
            continue
        # A same-path URL with the wrong station/ticker remains a different page.
        required = parse_qs(wanted.query)
        if any(current_q.get(k) != v for k, v in required.items()):
            continue
        return 'rule_page'
    return 'rule_authority' if authority else 'other_source'


def groups(text):
    """Keep local headings and complete Markdown or flattened table rows."""
    rows = list(legacy.lines(text))
    flat = {}; occupied = set()
    for i, (a, b, value) in enumerate(rows):
        if value.strip().casefold() != 'date':
            continue
        j = i + 1
        while j < len(rows) and j-i <= 10 and not DATE.fullmatch(rows[j][2].strip()):
            cell = rows[j][2].strip()
            if not cell or len(cell) > 80 or NUMBER.fullmatch(cell) or cell.startswith(('#', '|')):
                break
            j += 1
        headers = rows[i:j]
        if not 3 <= len(headers) <= 10 or j >= len(rows) or not DATE.fullmatch(rows[j][2].strip()):
            continue
        data = []; k = j
        while k < len(rows) and DATE.fullmatch(rows[k][2].strip()):
            end = k + len(headers)
            if end > len(rows) or not all(NUMBER.fullmatch(r[2].strip()) for r in rows[k+1:end]):
                break
            data.append((k, end)); k = end
        if len(data) < 2:
            continue
        for left, right in data:
            flat[left] = {'ranges': [[headers[0][0], headers[-1][1]], [rows[left][0], rows[right-1][1]]],
                          'kind': 'flat_table_row', 'table_structure_complete': True}
        occupied.update(range(i, k))

    heading = []; heading_at = {}
    for i, (a, b, value) in enumerate(rows):
        if i in occupied:
            heading_at[i] = list(heading)
            continue
        match = re.match(r'^\s*(#{1,6})\s+\S', value)
        if match:
            level = len(match[1]); heading = [h for h in heading if h[2] < level]
            # Bound context from the text itself, not a synthesized title/date.
            if b-a <= 350:
                heading.append((a, b, level))
        heading_at[i] = list(heading)
    starts = {a: i for i, (a, _, _) in enumerate(rows)}
    output = list(flat.values())
    available = handoff.uncovered(0, len(text), [(rows[i][0],rows[i][1]) for i in occupied])
    ordinary = []
    for left,right in available:
        for item in legacy.groups(text[left:right]):
            ordinary.append({**item, 'ranges':[[a+left,b+left] for a,b in item['ranges']]})
    for item in ordinary:
        main = item['ranges'][-1]
        i = starts.get(main[0], 0)
        contexts = heading_at.get(i, [])[-2:]
        output.append({**item, 'ranges': handoff.ranges([[a,b] for a,b,_ in contexts] + item['ranges']),
                       'heading_ranges': [[a,b] for a,b,_ in contexts]})
    # Add headings to flattened rows too; preserve the complete header and row.
    for item in output:
        if item['kind'] == 'flat_table_row':
            i = starts.get(item['ranges'][-1][0], 0)
            item['ranges'] = handoff.ranges([[a,b] for a,b,_ in heading_at.get(i, [])[-2:]] + item['ranges'])
            item['heading_ranges'] = [[a,b] for a,b,_ in heading_at.get(i, [])[-2:]]
    unique = {}
    for item in output:
        unique[(tuple(map(tuple, item['ranges'])), item['kind'])] = item
    return list(unique.values())


def signals(text, item, target, source, as_of):
    content = '\n'.join(text[a:b] for a,b in item['ranges'])
    # A whole-source prefix supplies entity context, not fabricated row values.
    identity = legacy.plain(text[:1100])
    main = text[slice(*item['ranges'][-1])]
    words = terms(main); scope = terms(content)
    topical = len(words & set(target['content_terms']))
    scoped = len(scope & set(target['content_terms']))
    entity = sum(n in legacy.plain(content) for n in target['name_hints'])
    context_entity = any(n in identity for n in target['name_hints'])
    row = item['kind'] in ('table_row', 'flat_table_row')
    # A dated numeric row in an entity-specific page can inherit the literal
    # page identity. This navigation signal does not verify the row's metric.
    if row and context_entity and dates(main):
        scoped += 2 if terms(identity) & set(target['content_terms']) else 0
    numeric_text = DATE.sub(' ', legacy.plain(main))
    numeric_text = re.sub(r'\b(?:19|20)\d{2}\b|\[\d+\]|\(#fn\d+\)', ' ', numeric_text)
    measurement = bool(re.search(r'(?<![\w.-])[-+]?\d[\d,]*(?:\.\d+)?',numeric_text))
    quantified = measurement and (topical >= 2 or (scoped >= 2 and context_entity))
    literals = dates(content)
    years = {int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', content)}
    target_years = set(target['literal_years'])
    same_year = bool(years & target_years)
    heading_dates = dates('\n'.join(text[a:b] for a,b in item.get('heading_ranges',[])))
    # A future date mentioned inside an old announcement is not its date label.
    dated_scope = heading_dates[:1] if heading_dates else literals
    previous = [date.fromisoformat(d['iso']) for d in dated_scope if date.fromisoformat(d['iso']) <= as_of] if as_of else []
    age = (as_of-max(previous)).days if previous else None
    recent = 3 if age is not None and age <= 45 else 2 if age is not None and age <= 120 else 1 if same_year else 0
    short_lines = [line.strip() for line in main.splitlines() if line.strip()]
    navigation = not row and len(short_lines) >= 8 and sum(len(v) <= 60 for v in short_lines)/len(short_lines) >= .8 and not quantified
    # Explainable capped signals. Raw counts and repeated URLs cannot dominate.
    score = min(5, topical)*6 + min(5, scoped)*2 + min(2, entity)*8
    score += 20*quantified + 12*same_year + 6*recent
    score += (12 if source['role'] == 'rule_page' else 4 if source['role'] == 'rule_authority' else 0) if scoped else 0
    score -= 24*navigation
    return {'score': score, 'content_term_matches': topical, 'scope_term_matches': scoped,
            'quantified_navigation_hint': quantified, 'literal_dates': literals[:8],
            'literal_heading_dates': heading_dates, 'heading_ranges':item.get('heading_ranges',[]),
            'literal_target_year_match': same_year, 'recency_hint': recent,
            'date_scope_verified': False, 'source_role': source['role'],
            'navigation_hint': navigation, 'row_structure': item['kind'], 'meaning_verified': False}


def pack(bundle, registry, *, limit=legacy.BYTE_CAP):
    reject_outcomes(bundle['request']); before = digest(bundle)
    packet = packet_for(bundle); question = packet['question']
    target = legacy.profile(question)
    target.update(content_terms=sorted(terms(question.get('question', ''))),
                  literal_years=sorted({int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', question.get('question',''))}))
    rule_urls = re.findall(r'https?://[^\s)<>]+', question.get('resolution_criteria',''))
    as_of = None
    try: as_of = datetime.fromisoformat(bundle['request']['as_of_utc'].replace('Z', '+00:00')).date()
    except (KeyError, TypeError, ValueError): pass
    # Keep the analysis instructions and registry exactly as the frozen control.
    state = chain.initial_state(packet)
    control, _ = legacy.pack(bundle, registry, limit=limit)
    state = {**state, **{k:v for k,v in control.items() if k not in ('evidence','sources','context_omitted')}}
    sources = {s['source_id']:copy.deepcopy(s) for s in packet['sources']}
    candidates = []; admissions = []; omissions = []; selected = set()
    for sid, source in sources.items():
        text = bundle['pages'][source['url']]['content']
        if hashlib.sha256(text.encode()).hexdigest() != source['body_sha256']:
            raise ValueError('Saved body checksum mismatch')
        meta = {'role':source_role(source['url'], rule_urls)}
        for item in groups(text):
            item = {**item, 'signals':signals(text,item,target,meta,as_of)}
            candidates.append((sid,item))

    def add(sid, item, role):
        key = (sid, tuple(map(tuple,item['ranges'])))
        if key in selected: return
        selected.add(key); trial = copy.deepcopy(state)
        if sid not in {s['source_id'] for s in trial['sources']}:
            trial['sources'].append({k:sources[sid][k] for k in
                ('source_id','url','body_sha256','capture_metadata','saved_body_truncated') if k in sources[sid]})
        trial['evidence'] = handoff.compact(trial['evidence'] + [
            {'source_id':sid,'start':a,'end':b} for a,b in item['ranges']], bundle['pages'], sources)
        record = {'source_id':sid,'ranges':item['ranges'],'role':role,'kind':item['kind'],
                  'table_structure_complete':item['table_structure_complete'], 'signals':item['signals']}
        if chain.request_bytes(trial,registry) > limit:
            omissions.append({**record,'reason':'request_byte_limit'}); return
        if trial['evidence'] == state['evidence']: return
        state.clear(); state.update(trial); admissions.append(record)

    # Preserve small literal source identity; keep authority-only or empty pages
    # auditable without awarding them the same space as a dated measurement.
    for sid, source in sources.items():
        text = bundle['pages'][source['url']]['content']
        first = list(handoff.identity_atoms(text))[:1]
        if first:
            add(sid, {'ranges':[first[0]],'kind':'source_identity','table_structure_complete':False,
                'signals':{'source_role':source_role(source['url'],rule_urls),'meaning_verified':False}}, 'literal_identity')
    ordered = sorted(candidates, key=lambda pair:(pair[1]['signals']['quantified_navigation_hint'],
        pair[1]['signals']['literal_target_year_match'],pair[1]['signals']['recency_hint'],
        pair[1]['signals']['score'], -pair[1]['ranges'][-1][0], pair[0]), reverse=True)
    for sid,item in ordered:
        if item['signals']['scope_term_matches'] >= 2 and not item['signals']['navigation_hint']:
            add(sid,item,'ranked_local_context')
        else:
            omissions.append({'source_id':sid,'ranges':item['ranges'],'kind':item['kind'],
                              'reason':'lower_navigation_priority','signals':item['signals']})
    state['context_omitted'] = any(handoff.uncovered(0,len(bundle['pages'][s['url']]['content']),
        [(e['start'],e['end']) for e in state['evidence'] if e['source_id']==sid]) for sid,s in sources.items())
    if audit_spans(bundle,state) or digest(bundle) != before:
        raise ValueError('Immutable source or coordinate audit failed')
    if chain.request_bytes(state,registry)>limit or not state['evidence']:
        raise ValueError('No valid bounded original evidence packet')
    return state, {'schema':PROTOCOL,'source_bundle_sha256':before,'state_sha256':digest(state),
        'request_bytes':chain.request_bytes(state,registry),'request_byte_limit':limit,
        'target_profile':target,'admissions':admissions,'omissions':omissions,
        'raw_snapshots_preserved':True,'meaning_verified':False,
        'model_http':0,'searches':0,'fetches':0,'submitted':False}
