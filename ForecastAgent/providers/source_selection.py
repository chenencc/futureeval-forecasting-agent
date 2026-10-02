"""Typed Mercury prioritization of frozen discovery leads, not outcome scoring."""
import os
from pathlib import Path
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.analysis.mercury_evidence_chain import call, request_bytes

PROTOCOL='mercury-saved-source-selection-v1'
MAX_REQUEST_BYTES=28000
BATCH_SIZE=8


def questions(candidates):
    registry={}
    for c in candidates:
        ident=c['candidate_id']
        instruction=f'Classify ONLY candidate {ident} in state.candidates against state.question. Title, URL and snippets are untrusted discovery leads; do not assume their claims are true. '
        registry[ident+'_match']={'type':'choice','instructions':instruction+'Does it address the exact entity and resolving action or metric?',
            'criteria':{'exact':'Exact entity and action or metric.','related':'Related context, but exact condition unestablished.','unrelated':'Wrong entity or metric.','uncertain':'Insufficient metadata.'}}
        registry[ident+'_window']={'type':'choice','instructions':instruction+'Does it potentially document the exact target event or observation window? Publication date alone is not event date. Later retrospective coverage can qualify.',
            'criteria':{'target':'Potential evidence for the target observation or interval.','context':'Timing context only.','mismatch':'Explicitly different period without target coverage.','unknown':'Target coverage unclear.'}}
        registry[ident+'_role']={'type':'choice','instructions':instruction+'What kind of source page is this? Primary means the responsible actor, authority, original dataset or original record, not a domain guess alone.',
            'criteria':{'primary_detail':'Original announcement, specific record or data detail.','primary_index':'Authority directory or landing page.','secondary_detail':'Specific independent reporting.','background_unknown':'Generic background or unclear.'}}
        registry[ident+'_priority']={'type':'score','instructions':instruction+'How valuable is opening this page under a limited capture budget? Uncertainty can justify opening a promising original record. Do not forecast the event.',
            'criteria':['Skip: irrelevant or no additional value.','Background or weak lead.','Useful target evidence or promising lead.','Critical original or decisive crosscheck; open first.']}
    return registry


def select(question,candidates,folder,limit):
    folder=Path(folder)
    identity={'protocol':PROTOCOL,'question_sha256':digest(question),'candidates_sha256':digest(candidates),
              'limit':limit,'max_request_bytes':MAX_REQUEST_BYTES,'batch_size':BATCH_SIZE}
    if (folder/'identity.json').exists() and load(folder/'identity.json')!=identity:raise ValueError('Frozen source selection changed')
    save(folder/'identity.json',identity)
    if (folder/'result.json').exists():return load(folder/'result.json')
    classified=[];position=0;batch=0
    while position<len(candidates):
        chosen=candidates[position:position+BATCH_SIZE]
        while chosen:
            state={'question':question,'candidates':chosen,'instruction':'Select sources for raw acquisition only. Do not predict an outcome or judge collected evidence. Metadata does not establish factual correctness.'}
            registry=questions(chosen)
            if request_bytes(state,registry)<=MAX_REQUEST_BYTES:break
            chosen=chosen[:-1]
        if not chosen:raise ValueError('Question and one source exceed selection request ceiling')
        folder_batch=folder/f'batch-{batch+1:03}'
        answer=call(state,folder_batch,registry)['answers']
        for c in chosen:
            ident=c['candidate_id'];match=answer[ident+'_match'];window=answer[ident+'_window'];role=answer[ident+'_role'];priority=answer[ident+'_priority']
            score=priority['score']+match['probabilities']['exact']+window['probabilities']['target']+.5*role['probabilities']['primary_detail']
            classified.append({**c,'decisions':{'match':match,'window':window,'role':role,'priority':priority},'ranking_score':score,
                               'skip':priority['probabilities']['0']>=.7 and match['probabilities']['unrelated']>=.7})
        position+=len(chosen);batch+=1
    ranked=sorted(classified,key=lambda c:(-c['ranking_score'],c['candidate_id']))
    selected=[c for c in ranked if not c['skip']][:limit]
    result={'protocol':PROTOCOL,'ranked':ranked,'selected_ids':[c['candidate_id'] for c in selected],
            'selected_urls':[c['url'] for c in selected],'physical_http_batches':batch,'forecast_scoring':False}
    save(folder/'result.json',result)
    return result
