"""Canonical model view backed by a locally built, hash-bound capture index.

This is a format/admission boundary, not a general semantic relevance classifier.
The source index is built by the application, never by the model or source prose.
"""
from copy import deepcopy
import hashlib,json

POLICY='frozen-source-canonical-input/1'
CONTEXT='_canonical_context'

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def text_hash(text):return hashlib.sha256(text.encode()).hexdigest()

def ordered(value):
    # Lists carry semantics (options, bins, table cells) and are never reordered.
    if isinstance(value,dict):return {k:ordered(value[k]) for k in sorted(value)}
    if isinstance(value,list):return [ordered(x) for x in value]
    return deepcopy(value)

def _excluded(url,source):
    binding=source.get('source_bindings',{}).get(url,{})
    reviewed=source.get('original_reading',{}).get('reviewed_source_ids',[])
    relevance=binding.get('relevance') or {};span=relevance.get('span') or {}
    body=source.get('pages',{}).get(url,{}).get('content','')
    a,b=span.get('start'),span.get('end')
    return (binding.get('source_id') in reviewed and binding.get('inclusion')=='excluded'
        and binding.get('forecast_relevance')=='none'
        and relevance.get('entity')=='mismatch' and relevance.get('metric')=='mismatch'
        and type(a)==int and type(b)==int and 0<=a<b<=len(body))

def build_context(packet,source):
    """Index literal candidates already verified against saved body hashes."""
    aliases={x['source_id']:x['urls'] for x in packet['evidence_packing']['aliases']}
    records=[]
    for s in packet['sources']:
        urls=sorted(set(aliases.get(s['source_id'],[s['url']])))
        spans={}
        for e in packet['evidence']:
            if e['source_id']!=s['source_id']:continue
            # Independently bind each index entry to the actual saved body.
            body=source['pages'][s['url']]['content']
            if text_hash(body)!=s['body_sha256'] or body[e['start']:e['end']]!=e['text']:
                raise ValueError('CANONICAL_CAPTURE_BINDING_MISMATCH')
            spans.setdefault(text_hash(e['text']),[]).append([e['start'],e['end']])
        from .decision_relevance import exclusion
        reasons=[]
        for u in urls:
            reason=exclusion(packet['question'],u,source['pages'][u],source.get('analysis_selection_reviews',{}).get(u))
            if source.get('source_bindings',{}).get(u,{}).get('forecast_relevance') in {'direct','baseline','driver'}:
                reason=None  # A conflicting new review cannot silently erase existing substantive evidence.
            reasons.append(reason or ('reviewed_entity_and_metric_mismatch' if _excluded(u,source) else None))
        records.append(dict(url=min(urls),aliases=urls,body_sha256=s['body_sha256'],
            capture_metadata=deepcopy(s.get('capture_metadata',{})),
            saved_body_truncated=s.get('saved_body_truncated',False),spans=spans,
            exclusion=','.join(sorted(set(reasons))) if all(reasons) else None))
    value=dict(policy=POLICY,question_sha256=digest(packet['question']),sources=records)
    value['sha256']=digest(value)
    return value

def render(state,questions):
    """Return model state/registry and a separate local normalization receipt."""
    context=state.get(CONTEXT)
    if not isinstance(context,dict) or context.get('policy')!=POLICY:raise ValueError('CANONICAL_CONTEXT_REQUIRED')
    check={k:v for k,v in context.items() if k!='sha256'}
    if digest(check)!=context.get('sha256'):raise ValueError('CANONICAL_CONTEXT_HASH_MISMATCH')
    if digest(state.get('question'))!=context['question_sha256']:raise ValueError('CANONICAL_QUESTION_CHANGED')
    index={}
    for s in context['sources']:
        for url in s['aliases']:
            if url in index:raise ValueError('CANONICAL_AMBIGUOUS_SOURCE')
            index[url]=s
    source_map={}
    for s in state.get('sources',[]):
        if s['source_id'] in source_map:raise ValueError('CANONICAL_DUPLICATE_SOURCE_ID')
        source_map[s['source_id']]=s
    admitted=[];sources={};excluded=[];mapping=[]
    for e in state.get('evidence',[]):
        supplied=source_map.get(e.get('source_id'))
        if supplied is None:raise ValueError('CANONICAL_DANGLING_SOURCE')
        original=index.get(supplied.get('url'))
        if original is None:
            excluded.append(dict(source_id=e.get('source_id'),reason='source_outside_frozen_capture',text_sha256=text_hash(e['text'])))
            continue
        if original['exclusion']:
            excluded.append(dict(source_id=e.get('source_id'),reason=original['exclusion'],text_sha256=text_hash(e['text'])))
            continue
        for item in (supplied,e):
            if item.get('body_sha256') is not None and item['body_sha256']!=original['body_sha256']:
                raise ValueError('CANONICAL_BODY_HASH_CHANGED')
            if item.get('url') is not None and item['url'] not in original['aliases']:
                raise ValueError('CANONICAL_SOURCE_URL_CHANGED')
        locations=original['spans'].get(text_hash(e['text']),[])
        if not locations:raise ValueError('CANONICAL_UNBOUND_TEXT')
        if 'start' in e or 'end' in e:
            location=[e.get('start'),e.get('end')]
            if location not in locations:raise ValueError('CANONICAL_SPAN_CHANGED')
        else:
            unique={tuple(x) for x in locations}
            if len(unique)!=1:raise ValueError('CANONICAL_AMBIGUOUS_SPAN')
            location=list(next(iter(unique)))
        source_key=(original['url'],original['body_sha256'])
        sid='S'+digest(source_key)[:12]
        eid='E'+digest([source_key,location])[:12]
        sources[source_key]=dict(source_id=sid,url=original['url'],capture_metadata=deepcopy(original['capture_metadata']),saved_body_truncated=original['saved_body_truncated'])
        admitted.append((source_key,location,dict(source_id=sid,evidence_id=eid,text=e['text'])))
        mapping.append(dict(original_source_id=e.get('source_id'),original_evidence_id=e.get('evidence_id'),canonical_source_id=sid,canonical_evidence_id=eid,body_sha256=original['body_sha256'],start=location[0],end=location[1]))
    if state.get('evidence') and not admitted:raise ValueError('CANONICAL_NO_ADMITTED_EVIDENCE')
    # Preserve original order within a document, including table headers and rows.
    admitted.sort(key=lambda x:(x[0],x[1][0],x[1][1]))
    evidence=[];seen=set()
    for _,_,e in admitted:
        if e['evidence_id'] not in seen:evidence.append(e);seen.add(e['evidence_id'])
    wire={k:deepcopy(v) for k,v in state.items() if k not in {CONTEXT,'sources','evidence'}}
    wire['sources']=[sources[k] for k in sorted(sources)];wire['evidence']=evidence
    audit=dict(policy=POLICY,context_sha256=context['sha256'],excluded=excluded,mapping=mapping,
        semantic_relevance_certified=False,original_artifacts_unchanged=True)
    return ordered(wire),ordered(questions),audit
