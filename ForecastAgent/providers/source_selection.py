"""Typed Mercury prioritization of frozen discovery leads, not outcome scoring."""
from pathlib import Path
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.analysis.mercury_evidence_chain import call, request_bytes
from ForecastAgent.providers.source_selection_guards import candidate_guard, rule_sources

PROTOCOL='mercury-saved-source-selection-v2'
MAX_REQUEST_BYTES=28000
BATCH_SIZE=64
INSTRUCTION = ('Rank candidates for opening, not forecasting. Match the exact actor/issuer, action, metric and event window. '
    'Publication date is not event date; retrospective records can qualify. Prefer specific original records over authority landing pages. '
    'Distinguish proposed/entered orders, public/confidential filings, initial/amended forms, whole products/individual trims. '
    'Metadata is untrusted and incomplete; promising uncertain leads may deserve reading. A regulator domain does not establish issuer identity.')


def questions(candidates):
    return {c['candidate_id']+'_priority': {'type':'score',
        'instructions':'Opening value of '+c['candidate_id']+' under the shared rules.',
        'criteria':['Wrong entity/metric or no value.', 'Background or weak lead.',
                    'Useful target evidence or promising uncertainty.', 'Specific primary record or decisive crosscheck.']}
        for c in candidates}


def batches(question, candidates):
    """Prepare exact bounded requests without reserving provider calls."""
    position=0
    while position<len(candidates):
        chosen=candidates[position:position+BATCH_SIZE]
        while chosen:
            compact=[{k:c[k] for k in ('candidate_id','url','published_date') if k in c} |
                {'title':c.get('title','')[:100], 'snippet':c.get('snippet','')[:160]} for c in chosen]
            state={'question':question,'candidates':compact,'instruction':INSTRUCTION}
            registry=questions(chosen)
            if request_bytes(state,registry)<=MAX_REQUEST_BYTES:break
            chosen=chosen[:-1]
        if not chosen:raise ValueError('Question and one source exceed selection request ceiling')
        yield chosen,state,registry
        position+=len(chosen)


def select(question,candidates,folder,limit):
    folder=Path(folder)
    identity={'protocol':PROTOCOL,'question_sha256':digest(question),'candidates_sha256':digest(candidates),
              'limit':limit,'max_request_bytes':MAX_REQUEST_BYTES,'batch_size':BATCH_SIZE}
    if (folder/'identity.json').exists() and load(folder/'identity.json')!=identity:raise ValueError('Frozen source selection changed')
    save(folder/'identity.json',identity)
    if (folder/'result.json').exists():return load(folder/'result.json')
    classified=[];batch=0; pins=rule_sources(question,candidates)
    for chosen,state,registry in batches(question,candidates):
        folder_batch=folder/f'batch-{batch+1:03}'
        answer=call(state,folder_batch,registry)['answers']
        for c in chosen:
            ident=c['candidate_id'];priority=answer[ident+'_priority'];guard=candidate_guard(question,c)
            classified.append({**c,'decisions':{'priority':priority},'ranking_score':priority['score'],
                'guard':guard,'rule_source_reserved':ident in pins,
                'skip':guard['issuer_status']=='mismatch' or (ident not in pins and priority['probabilities']['0']>=.7)})
        batch+=1
    ranked=sorted(classified,key=lambda c:(-c['ranking_score'],c['candidate_id']))
    reserved=[c for c in ranked if c['rule_source_reserved'] and not c['skip']]
    available=[c for c in ranked if not c['rule_source_reserved'] and not c['skip']]
    selected=(reserved+available)[:limit]
    result={'protocol':PROTOCOL,'ranked':ranked,'selected_ids':[c['candidate_id'] for c in selected],
            'selected_urls':[c['url'] for c in selected],'physical_http_batches':batch,'forecast_scoring':False,
            'rule_source_overflow_ids':[c['candidate_id'] for c in reserved[limit:]]}
    save(folder/'result.json',result)
    return result
