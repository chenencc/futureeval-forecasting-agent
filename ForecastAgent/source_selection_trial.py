"""Frozen binary acquisition comparison with fresh, isolated experiment ledgers."""
import argparse
import copy
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from ForecastAgent.analysis.pilot import digest,load,save
from ForecastAgent.providers.source_selection import select
from ForecastAgent.providers.tavily_search import canonical_url
from ForecastAgent.retrieval_sources import allowed_source,source_urls
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.supplement.stage import run as supplement,analysis_overlay

IDS=['42491','40695','44547','43822','43461','42489','36871','40852','40849','41206']
PROTOCOL='binary-ten-saved-selection-fresh-acquisition-v1'


def freeze_candidates(bundle):
    """Exclude page bodies, prior analysis, and model outcome labels from selection."""
    candidates={}
    def add(url,hit=None,origin='saved_search'):
        hit=hit or {};key=canonical_url(url)
        if not key or not allowed_source(url):return
        row=candidates.setdefault(key,{'url':url,'title':'','snippet':'','published_date':None,'origins':[]})
        row['title']=row['title'] or hit.get('title','')
        row['snippet']=row['snippet'] or (hit.get('content') or hit.get('text') or '')[:800]
        row['published_date']=row['published_date'] or hit.get('published_date')
        if origin not in row['origins']:row['origins'].append(origin)
    for search in bundle.get('searches',[])+bundle.get('exa_searches',[]):
        for hit in search.get('results',[]):add(hit['url'],hit)
    for field in ['resolution_criteria','fine_print','background']:
        for url in source_urls(bundle['request'].get(field,'')):add(url,origin='question_link')
    old=[]
    for attempt in bundle.get('fetch_attempts',[]):
        url=attempt.get('url');channel=attempt.get('channel')
        if url and channel not in {'polymarket','archive_lookup'} and allowed_source(url):
            key=canonical_url(url)
            if key not in {canonical_url(u) for u in old}:old.append(url)
            add(url,origin='prior_capture_url_metadata_only')
    rows=[dict(row,candidate_id=f'C{i+1:03}') for i,row in enumerate(candidates.values())]
    if not rows or not old:raise ValueError('Saved discovery and old capture selection required')
    return rows,old[:8]


def selection_question(bundle):
    return {key:copy.deepcopy(bundle['request'][key]) for key in
        ['id','question','resolution_criteria','fine_print','background','open_time','close_time','scheduled_close_time','scheduled_resolve_time'] if key in bundle['request']}


def capture_arm(parent,candidates,urls,folder,arm,comparison_identity):
    request=selection_question(parent)
    request.update(mode='live',pipeline='collection',question_type='binary',acquisition_profile='collection_v3',
        acquisition_focus='raw_recall',exa_search_policy='optional',experiment={'protocol':PROTOCOL,'arm':arm,'identity':comparison_identity})
    task=RetrievalTask(folder/'acquisition',request)
    if not (folder/'acquisition/bundle.json').exists():
        # User explicitly authorized new experimental ledgers, not mutation of old quotas.
        task.bundle['plan']=copy.deepcopy(parent['plan'])
        for c in candidates:task.bundle['source_leads'][canonical_url(c['url'])]={'url':c['url'],'origin':'frozen_discovery_replay','published_date':c['published_date']}
        task.bundle['selection_urls']=urls;task.bundle['budget_grant']={'reason':'User authorized fresh acquisition-only comparison on ten binary cases','old_bundle_sha256':digest(parent),'old_ledger_mutated':False,'paid_search_calls':0,'max_acquisition_fetches':8}
        task.save()
    elif task.bundle.get('selection_urls')!=urls:raise ValueError('Frozen acquisition selection changed')
    if not task.bundle.get('result'):
        for start in range(0,len(urls),4):
            task.execute('read_sources',{'urls':urls[start:start+4],'rescue_failed':False},'')
        task.bundle['result']={'status':'partial','gaps':[],'no_forecasts_submitted':True,'selection_protocol':PROTOCOL}
        task.save()
    bundle=task.bundle
    archive=folder/'parent.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('campaign.json',json.dumps({'tasks':{request['id']:{'status':'closed_with_gaps'}}}))
        z.writestr(f"tasks/{request['id']}/bundle.json",json.dumps(bundle))
    summary=supplement(archive,folder/'supplement',[request['id']],network=True)
    overlay=analysis_overlay(bundle,folder/'supplement',request['id']);save(folder/'collected.json',overlay)
    return {'selected_urls':urls,'physical_fetch_attempts':len(bundle['fetch_attempts']),
            'readable_original_pages':len(bundle['pages']),'readable_pages_after_supplement':len(overlay['pages']),
            'failed_fetches':sum(r['status']=='failed' for r in bundle['fetch_attempts']),
            'supplement':summary,'tavily_attempts':len(bundle['searches']),'exa_attempts':len(bundle['exa_searches'])}


def run(inputs,output,batch):
    inputs=Path(inputs);output=Path(output);ids=IDS[(batch-1)*5:batch*5]
    output.mkdir(parents=True,exist_ok=True)
    if len(ids)!=5:raise ValueError('Frozen ten cases, five per batch')
    manifest={'protocol':PROTOCOL,'ids':ids,'source_run':36948699455,'scope':'acquisition_only',
        'fresh_budget_authorized':True,'new_paid_searches':0,'forecast_submissions':0,'analysis_calls':0,
        'arms':['old_selection_recaptured','mercury_selection'],'acquisition_fetch_cap_per_arm':8,
        'capture_quota_per_arm':'Same top-K: min(8, prior unique page attempts); Mercury may skip unrelated leads.',
        'source_sha256':{ident:digest(load(inputs/'tasks'/ident/'bundle.json')) for ident in ids}}
    with task_lock(output):
        if (output/'manifest.json').exists() and load(output/'manifest.json')!=manifest:raise ValueError('Frozen trial changed')
        save(output/'manifest.json',manifest);rows=[]
        for ident in ids:
            folder=output/'tasks'/ident
            try:
                parent=load(inputs/'tasks'/ident/'bundle.json');save(folder/'baseline-original.json',parent)
                candidates,old=freeze_candidates(parent);save(folder/'candidates.json',candidates)
                # Selection state includes only discovery metadata, not capture history.
                state_candidates=[{k:v for k,v in c.items() if k!='origins'} for c in candidates]
                print(json.dumps({'id':ident,'stage':'mercury_selection','candidates':len(candidates),'top_k':len(old)}),flush=True)
                selection=select(selection_question(parent),state_candidates,folder/'selection',len(old))
                arms={}
                # Alternate arm order to reduce systematic freshness effects.
                order=['old_selection_recaptured','mercury_selection'] if IDS.index(ident)%2==0 else ['mercury_selection','old_selection_recaptured']
                for arm in order:
                    print(json.dumps({'id':ident,'stage':'capture','arm':arm}),flush=True)
                    urls=old if arm=='old_selection_recaptured' else selection['selected_urls']
                    arms[arm]=capture_arm(parent,candidates,urls,folder/arm,arm,digest(manifest))
                row={'id':ident,'question':parent['request']['question'],'status':'completed','candidate_count':len(candidates),
                     'top_k':len(old),'mercury_batches':selection['physical_http_batches'],'arms':arms,
                     'original_readable_pages':len(parent.get('pages',{})),'analysis_calls':0,'forecast_submissions':0}
            except Exception as exc:
                row={'id':ident,'status':'failed','error':str(exc)};save(folder/'failure.json',row)
            rows.append(row);save(output/'report.json',{'rows':rows,'protocol':PROTOCOL,'forecast_submissions':0})
            print(json.dumps(row),flush=True)
        if any(r['status']=='failed' for r in rows):raise RuntimeError('Preserved trial failure; resume without resetting experimental reservations')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--inputs',required=True);p.add_argument('--output',required=True)
    p.add_argument('--batch',type=int,choices=[1,2],required=True);args=p.parse_args();run(args.inputs,args.output,args.batch)
