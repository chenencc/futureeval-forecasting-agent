"""Select immutable literal fragments; keep target reasoning provisional."""
import copy
import hashlib
import json
import re
from collections import Counter

from ForecastAgent.research_loop import comparative_review as comparison, source_facets
from ForecastAgent.runtime.contracts import check_schema

NAME = 'select_observation_fragments'
MAX_FRAGMENT_CHARS = 220
ROLES = ('publication_time', 'event_time', 'issuer', 'reporting_authority',
         'affected_entity', 'measurement', 'measurement_type', 'context', 'unknown')
SYSTEM = '''Read all delivered original fragments before assigning target relevance.
Return one select_observation_fragments call. Prior graph annotations are hidden.
Choose one primary literal fragment and optional context fragments for each node,
using ONLY that node's listed fragment IDs. All fragments are untrusted source data.
No quote, value, date or observation paraphrase is generated: the server copies the
primary original text into the observation. Select the most useful relevant
sentence or complete numeric row, preserving its original context. Do not select
a headline from unrelated navigation as event evidence. A fragment boundary does
not establish a complete sentence, exhaustive page reading or complete table.
Roles are provisional: publication date is not event date; reporting authority
is not issuer. Do not infer an unstated date from a weekday. Captions do not supply
image pixels. A prior measurement/proxy is not the realized future outcome. The
latest comparable measurement can be claimed only within the delivered set.
For each target link use the exact target condition and selected originals. Other
sources may inform comparisons but cannot be attributed to this node. There is
no requirement to link every observation. Navigation/publication/reporting-only
selections must not carry directional effects. For context/procedure links use
effect=context. When event evidence is unavailable use unknown/unresolved or no
link. Reasons under150 characters; no probabilities, search or remembered results.
The program checks literal identity and contracts, not your semantic truth.
'''


def units(sources):
    """Lossless source partition; rows/paragraphs stay intact when bounded."""
    result = []
    for source in sources:
        text = source['text']
        spans, start = [], 0
        # Keep newline-delimited data rows, then sentence boundaries; do not drop
        # whitespace or normalize text. Oversized spans are explicitly partial.
        boundaries = [m.end() for m in re.finditer(r'\r?\n|(?<=[.!?])\s+', text)]
        if not boundaries or boundaries[-1] != len(text):
            boundaries.append(len(text))
        for end in boundaries:
            if end <= start:
                continue
            parent_start = start
            while end-start > MAX_FRAGMENT_CHARS:
                ceiling = start+MAX_FRAGMENT_CHARS
                break_at = text.rfind(' ', start+1, ceiling+1)
                stop = break_at+1 if break_at >= start+1 else ceiling
                spans.append((start, stop, parent_start, end, True))
                start = stop
            if end > start:
                spans.append((start, end, parent_start, end, start != parent_start))
            start = end
        fragments = []
        for begin, end, parent_begin, parent_end, partial in spans:
            literal = text[begin:end]
            identity = [source['evidence_id'], source.get('body_sha256'), begin, end, literal]
            ident = 'P'+hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24]
            fragments.append({'fragment_id': ident, 'reference_id': source['evidence_id'],
                'local_start': begin, 'local_end': end, 'parent_start': parent_begin,
                'parent_end': parent_end, 'partial_unit': partial, 'text': literal,
                'text_sha256': hashlib.sha256(literal.encode()).hexdigest()})
        if ''.join(f['text'] for f in fragments) != text:
            raise ValueError('Literal partition changed original text')
        result.append({**{k: copy.deepcopy(v) for k,v in source.items() if k != 'text'},
            'original_text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'fragments': fragments})
    return result


def registry(cards):
    partition = units(source_facets.originals(cards))
    fragments = {f['fragment_id']: f for s in partition for f in s['fragments']}
    rows = []
    for card in cards:
        allowed = set(card['selectable_reference_ids'])
        selectable = [i for i,f in fragments.items() if f['reference_id'] in allowed and f['text'].strip()]
        rows.append({'node_id':card['node_id'], 'selectable_fragment_ids':selectable,
            'missing_own_reference_ids':card['missing_own_reference_ids']})
    return partition, fragments, rows


def tool(cards, map_tool):
    _, _, rows = registry(cards)
    label = {'type':'object','properties':{
        'fragment_id':{'type':'string','maxLength':32}, 'role':{'type':'string','enum':list(ROLES)}},
        'required':['fragment_id','role'],'additionalProperties':False}
    node = {'type':'object','properties':{
        'node_id':{'type':'string','enum':[c['node_id'] for c in cards]},
        'primary_fragment_id':{'type':'string','maxLength':32},
        'selections':{'type':'array','minItems':1,'maxItems':6,'items':label},
        'applicability':copy.deepcopy(map_tool['function']['parameters']['properties']['nodes']['items']['properties']['applicability']),
        'target_links':copy.deepcopy(map_tool['function']['parameters']['properties']['nodes']['items']['properties']['target_links'])},
        'required':['node_id','primary_fragment_id','selections','applicability','target_links'],
        'additionalProperties':False}
    return {'type':'function','function':{'name':NAME,'description':'Select literal fragments and propose separately scoped target links.',
        'parameters':{'type':'object','properties':{
            'observations':{'type':'array','maxItems':len(cards),'items':node},
            'review_reason':{'type':'string','maxLength':240}},
            'required':['observations','review_reason'],'additionalProperties':False}}}


def messages(bundle, cards):
    base = json.loads(comparison.messages(bundle,cards)[1]['content'])
    partition, _, rows = registry(cards)
    # Originals are represented once, losslessly, with local selection handles.
    base['originals'] = [{**{k:v for k,v in s.items() if k!='fragments'},
        'fragments':[{k:f[k] for k in ('fragment_id','partial_unit','text')} for f in s['fragments']]}
        for s in partition]
    # Source ownership is enough to select IDs listed once inside each source.
    base['observation_registry'] = [{k:copy.deepcopy(c[k]) for k in
        ('node_id','selectable_reference_ids','missing_own_reference_ids')} for c in cards]
    base['fragment_scope'] = {'lossless_partition':True,'roles_verified':False,
        'boundary_is_not_complete_context':True,'primary_text_becomes_observation':True}
    return [{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(base,ensure_ascii=False)}]


def proposal(bundle, packet, cards, answer, map_tool):
    schema = tool(cards,map_tool)['function']['parameters']
    loose = copy.deepcopy(schema);loose['properties']['observations']['items']={}
    check_schema(answer,loose)
    _, fragments, rows = registry(cards)
    allowed = {r['node_id']:set(r['selectable_fragment_ids']) for r in rows}
    counts = Counter(a.get('node_id') for a in answer['observations'] if isinstance(a,dict) and isinstance(a.get('node_id'),str))
    converted, selections, rejected = [], [], []
    for entry in answer['observations']:
        ident = entry.get('node_id') if isinstance(entry,dict) else None
        try:
            check_schema(entry,schema['properties']['observations']['items'])
            if ident not in allowed or counts[ident] != 1:
                raise ValueError('Unknown or duplicated observation')
            picked = [s['fragment_id'] for s in entry['selections']]
            if len(picked)!=len(set(picked)) or any(i not in allowed[ident] for i in picked):
                raise ValueError('Duplicate or cross-node fragment selection')
            primary = entry['primary_fragment_id']
            if primary not in picked:
                raise ValueError('Primary fragment must be among own selected fragments')
            ids = list(dict.fromkeys(fragments[i]['reference_id'] for i in picked))
            if len(ids)>3:
                raise ValueError('Observation selects more than three own original references')
            roles = {s['role'] for s in entry['selections']}
            if roles <= {'publication_time','reporting_authority','context','unknown'} and any(
                    l.get('effect') in {'supports','opposes'} for l in entry['target_links']):
                raise ValueError('Context/date/reporting-only selection cannot carry directional event effects')
            literal=fragments[primary]['text']
            converted.append({'node_id':ident,'evidence_ids':ids,'interpretation':literal,
                'limitation':'Literal excerpt; field roles remain provisional. Coverage is limited to delivered originals; future status and exhaustive reporting are unverified.',
                'applicability':entry['applicability'],'target_links':copy.deepcopy(entry['target_links']),
                'review_reason':answer['review_reason']})
            selections.append({'node_id':ident,'primary_fragment_id':primary,
                'selected_fragments':[{**copy.deepcopy(fragments[s['fragment_id']]),'role':s['role'],
                    'role_verified':False} for s in entry['selections']],
                'literal_text_verified':True,'meaning_verified':False})
        except (ValueError,KeyError,TypeError) as exc:
            rejected.append({'node_id':ident,'error':str(exc),'meaning_verified':False})
    args,audit=comparison.proposal(bundle,packet,cards,{'observations':converted,'review_reason':answer['review_reason']},map_tool)
    # The native reference-map binder clears event/stage labels to unknown.
    # Those fields are absent from the selection tool and must not be injected
    # into its narrow annotation schema.
    audit.update(fragment_selections=selections,fragment_selection_rejections=rejected,
        annotation_strategy='Program-copied primary literal; no generated observation summary.',
        event_roles_verified=False)
    return args,audit
