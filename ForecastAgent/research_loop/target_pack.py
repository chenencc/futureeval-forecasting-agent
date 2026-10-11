"""Opt-in target-first delivery from immutable saved bodies, without HTTP.

Raw snapshots are preserved. Unlike the release control, old visible passages
are not mandatory. Ranking is a navigation heuristic, never a relevance verdict.
"""
import copy
import hashlib
import re
from urllib.parse import urlparse

from ForecastAgent.acquisition import handoff
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.acquisition.pipeline import reject_outcomes
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest, WARNING
from ForecastAgent.competition.mercury import packet_for

PROTOCOL = 'target-first-saved-context-v1'
BYTE_CAP = 22000
MAX_PASSAGE = 1800
STOP = set('will what which who when where how why is are was were would could should '
           'the a an of for to in on at by and or with from as this that these those '
           'it its be been being do does did than least most before after against '
           'resolve resolves resolution question forecast forecasts predict prediction '
           'according following official published publication yes no if otherwise '
           '2026 october september january february march april may june july august '
           'november december'.split())


def plain(text):
    """Rank visible labels rather than repeated URLs; keep original text intact."""
    return re.sub(r'\[([^\]]*)\]\([^)]*\)', r' \1 ', text).casefold()


def profile(question):
    title = question.get('question', '')
    tokens = set(re.findall(r'[\w]+', title.casefold())) - STOP
    # Candidate names are literal title phrases, not generated entity aliases.
    names = re.findall(r'\b[A-Z][\w.-]*(?:\s+[A-Z][\w.-]*)*', title)
    anchors = []
    for name in names:
        words = [w for w in name.split() if w.casefold() not in STOP]
        if words and (len(words) > 1 or len(words[0]) >= 3):
            anchors.append(' '.join(words).casefold())
    rule_terms = set(re.findall(r'[a-z]{4,}', question.get('resolution_criteria', '').casefold())) - STOP
    urls = re.findall(r'https?://[^\s)<>]+', question.get('resolution_criteria', ''))
    words = re.findall(r'[A-Za-z]+', title)
    metric_hints = [' '.join((a,b)).casefold() for a,b in zip(words,words[1:])
                    if a.islower() and b.islower() and a not in STOP and b not in STOP]
    return {'title_terms': sorted(tokens), 'name_hints': list(dict.fromkeys(anchors)),
            'metric_phrase_hints':sorted(set(metric_hints)),
            'rule_terms': sorted(rule_terms - tokens),
            'rule_hosts': sorted({urlparse(u).hostname for u in urls if urlparse(u).hostname}),
            'meaning_verified': False}


def lines(text):
    cursor = 0
    for value in text.splitlines(keepends=True):
        yield cursor, cursor + len(value), value
        cursor += len(value)


def groups(text):
    """Complete table row plus local headers; bounded contiguous prose slices."""
    rows = list(lines(text)); result = []; seen = set()
    def emit(ranges, kind, complete=True):
        ranges = handoff.ranges(ranges)
        key = tuple(map(tuple, ranges))
        if key and key not in seen:
            seen.add(key); result.append({'ranges': ranges, 'kind': kind,
                                         'table_structure_complete': complete})
    i = 0
    while i < len(rows):
        a, b, row = rows[i]
        if row.lstrip().startswith('|'):
            j = i
            while j < len(rows) and rows[j][2].lstrip().startswith('|'):
                j += 1
            block = rows[i:j]
            data_start = next((k for k, r in enumerate(block)
                if re.search(r'\|\s*[-+]?\d+(?:[.,]\d+)?\s*(?:\||$)', r[2])), len(block))
            headers = [(x,y) for x,y,_ in block[:data_start]]
            heading = []
            for x,y,value in reversed(rows[max(0,i-8):i]):
                if value.strip() and y-x <= 450:
                    heading = [(x,y)]; break
            for x,y,value in block[data_start:]:
                emit(heading + headers + [(x,y)], 'table_row',
                     bool(headers) and value.rstrip().endswith('|'))
            if data_start == len(block):
                emit(headers, 'table_header', False)
            i = j; continue
        if not row.strip():
            i += 1; continue
        j = i + 1
        while j < len(rows) and rows[j][2].strip() and not rows[j][2].lstrip().startswith('|'):
            if rows[j][2].lstrip().startswith('#') or rows[j][1] - a > MAX_PASSAGE:
                break
            j += 1
        end = rows[j-1][1]
        start = a
        while end-start > MAX_PASSAGE:
            right = start+MAX_PASSAGE
            boundary = max(text.rfind('\n', start+500, right), text.rfind('. ', start+500, right))
            right = boundary+1 if boundary >= start+500 else right
            emit([(start,right)], 'prose'); start = right
        emit([(start,end)], 'prose'); i = j
    return result


def rank(text, group, target):
    # Table headers are context, not a reason to rank every unrelated data row.
    a,b = group['ranges'][-1] if group['kind'] == 'table_row' else (0,0)
    content = plain(text[a:b] if group['kind'] == 'table_row' else
                    '\n'.join(text[x:y] for x,y in group['ranges']))
    words = set(re.findall(r'\w+', content))
    names = sum(bool(re.search(r'(?<!\w)'+re.escape(n)+r'(?!\w)', content))
                for n in target['name_hints'])
    primary = bool(target['name_hints'] and re.search(
        r'(?<!\w)'+re.escape(target['name_hints'][0])+r'(?!\w)',content))
    topic = len(words & set(target['title_terms']))
    rule = min(4, len(words & set(target['rule_terms'])))
    metrics = sum(m in content for m in target['metric_phrase_hints'])
    quantified = bool(re.search(r'\b\d+[.]\d+\b|\d\s*%',content))
    return (names*20 + 40*primary + topic*3 + rule + metrics*(10+15*quantified),
            int(group['kind'] == 'table_row' and names > 0), -group['ranges'][-1][0])


def pack(bundle, registry, *, limit=BYTE_CAP, required_groups=()):
    reject_outcomes(bundle['request'])
    original_sha = digest(bundle)
    # An in-progress raw snapshot has no closure result. Normalize only the
    # private delivery copy; absence of closure is not successful acquisition.
    packet_bundle = copy.deepcopy(bundle)
    result = packet_bundle.get('result')
    if result is not None and not isinstance(result, dict):
        raise ValueError('Saved collection result must be an object or uninitialized')
    if result is None:
        packet_bundle['result'] = {}
    packet = packet_for(packet_bundle); target = profile(packet['question'])
    sources = {s['source_id']:copy.deepcopy(s) for s in packet['sources']}
    state = chain.initial_state(packet)
    state.update(evaluation_warning=WARNING, as_of_utc=bundle['request'].get('as_of_utc'),
        instruction=state['instruction'] + ' Forecast the eventual resolution, not whether it is '
        'already observed. Earlier predictions are baselines, not realizations. Preserve source '
        'assumptions and scope limits. Capture time is not event time. Incomplete data widen '
        'uncertainty and do not imply NO or a low value.')
    if chain.request_bytes(state, registry) > limit:
        raise ValueError('Exact question and registry exceed the evidence byte cap')
    candidates, omitted, admitted, selected = {}, [], [], set()
    bound_groups = []
    by_url = {s['url']: sid for sid,s in sources.items()}
    # Pin whole source groups before heuristic fill. Graph references select
    # originals but cannot introduce paraphrases, transformed rows or new facts.
    for group in required_groups:
        trial = copy.deepcopy(state); ranges = []
        error = None
        for ref in group['references']:
            sid = by_url.get(ref['url']); source = sources.get(sid, {})
            text = bundle['pages'].get(ref['url'], {}).get('content', '')
            if (not sid or source.get('body_sha256') != ref['body_sha256'] or
                    type(ref['start']) is not int or type(ref['end']) is not int or
                    not 0 <= ref['start'] < ref['end'] <= len(text) or
                    ref.get('view_sha256', ref['body_sha256']) != ref['body_sha256']):
                error = 'invalid_original_binding'; break
            if sid not in {s['source_id'] for s in trial['sources']}:
                trial['sources'].append({k:source[k] for k in
                    ('source_id','url','body_sha256','capture_metadata','saved_body_truncated') if k in source})
            ranges.append({'source_id':sid,'start':ref['start'],'end':ref['end']})
        if not error:
            trial['evidence'] = handoff.compact(trial['evidence'] + ranges, bundle['pages'], sources)
            if chain.request_bytes(trial, registry) > limit:
                error = 'complete_group_exceeds_common_request_limit'
        bound_groups.append({'node_id':group['node_id'], 'status':'omitted' if error else 'delivered',
                             'reason':error, 'references':copy.deepcopy(group['references'])})
        if not error:
            state.clear(); state.update(trial)
    for sid, source in sources.items():
        text = bundle['pages'][source['url']]['content']
        if hashlib.sha256(text.encode()).hexdigest() != source['body_sha256']:
            raise ValueError('Saved body checksum mismatch')
        items = groups(text)
        for item in items:
            item['score'] = rank(text, item, target)
        candidates[sid] = sorted(items, key=lambda item:item['score'], reverse=True)

    def add(sid, item, role):
        key = (sid,tuple(map(tuple,item['ranges'])))
        if key in selected: return
        selected.add(key)
        trial = copy.deepcopy(state)
        if sid not in {s['source_id'] for s in trial['sources']}:
            trial['sources'].append({k:sources[sid][k] for k in
                ('source_id','url','body_sha256','capture_metadata','saved_body_truncated') if k in sources[sid]})
        trial['evidence'] = handoff.compact(trial['evidence'] + [
            {'source_id':sid,'start':a,'end':b} for a,b in item['ranges']], bundle['pages'], sources)
        record = {'source_id':sid, 'ranges':item['ranges'], 'role':role,
                  'navigation_score':list(item['score']), 'kind':item['kind'],
                  'table_structure_complete':item['table_structure_complete'], 'meaning_verified':False}
        if chain.request_bytes(trial, registry) > limit:
            omitted.append({**record,'reason':'request_byte_limit'}); return
        if trial['evidence'] == state['evidence']: return
        state.clear(); state.update(trial); admitted.append(record)

    # Admit each source's strongest group before filling another long article.
    for sid, items in sorted(candidates.items(), key=lambda pair:
            pair[1][0]['score'] if pair[1] else (-1,0,0), reverse=True):
        if items: add(sid,items[0],'source_best_target_context')
    # Literal prefix carries title/window context without certifying applicability.
    for sid, items in candidates.items():
        text = bundle['pages'][sources[sid]['url']]['content']
        first = list(handoff.identity_atoms(text))[:1]
        if first: add(sid,{'ranges':[first[0]],'kind':'source_identity','score':(0,0,0),
                          'table_structure_complete':False},'literal_source_identity')
    ordered = sorted(((sid,g) for sid,items in candidates.items() for g in items),
                     key=lambda pair:pair[1]['score'], reverse=True)
    for sid, item in ordered:
        if item['score'][0] > 0: add(sid,item,'target_ranked_context')
    state['context_omitted'] = any(handoff.uncovered(0,len(bundle['pages'][s['url']]['content']),
        [(e['start'],e['end']) for e in state['evidence'] if e['source_id']==sid]) for sid,s in sources.items())
    errors = audit_spans(bundle,state)
    if errors or digest(bundle) != original_sha:
        raise ValueError('Source integrity or immutable bundle preservation failure')
    if not state['evidence']:
        raise ValueError('No complete saved context fits the common input cap')
    return state, {'schema':PROTOCOL, 'source_bundle_sha256':original_sha,
        'state_sha256':digest(state), 'request_bytes':chain.request_bytes(state,registry),
        'request_byte_limit':limit, 'target_profile':target, 'admissions':admitted,
        'bound_original_groups':bound_groups,
        'omissions':omitted, 'unranked_or_zero_match_groups':sum(g['score'][0]<=0 for _,g in ordered),
        'raw_snapshots_preserved':True, 'old_visible_passages_mandatory':False,
        'truth_verified':False, 'model_http':0, 'searches':0, 'fetches':0, 'submitted':False}
