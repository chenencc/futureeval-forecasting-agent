"""Fresh paired acquisition with optional map feedback and fixed Mercury scoring."""
import argparse
import copy
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.acquisition import pipeline
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.research_loop import POLICY, decision, decision_http, fusion, live_trial, state
from ForecastAgent.research_loop.forecast_brief import registry
from ForecastAgent.research_loop.target_pack import pack
from ForecastAgent.research_loop.distribution import forecast
from ForecastAgent.runtime.task_lock import task_lock

PROTOCOL = 'paired-map-guided-acquisition-v1'
BUDGET = {'model_decisions':12, 'model_http':16, 'model_failures':2, 'seconds_per_arm':900,
          'tavily_basic':3, 'exa_search':1, 'initial_fetch':8, 'extract_batches':1,
          'supplement_browser':2, 'supplement_http':2, 'map_updates':3, 'score_http':1}
FIELDS = ('id','post_id','question','question_type','resolution_criteria','fine_print','background',
          'open_time','close_time','scheduled_resolve_time','options','scaling','inbound_outcome_count',
          'open_lower_bound','open_upper_bound','unit','source_question_url','official_rules_available')


def input_for(source, when, arm):
    request = {k:copy.deepcopy(source['request'][k]) for k in FIELDS if k in source['request']}
    pipeline.reject_outcomes(request)
    request.update(mode='live', as_of_utc=when, acquisition_focus='raw_recall', acquisition_strategy=pipeline.V3_STRATEGY, source_reading_policy='crawl4ai_v1',
                   exa_search_policy='required', collection_purpose='Local paired acquisition research; no submission')
    if arm == 'fusion':
        request.update(research_state_policy=POLICY, research_acquisition_policy=fusion.POLICY_NAME)
    return request


@contextmanager
def bounded_dispatch(used_http=0, used_decisions=0, used_failures=0, remaining_seconds=None):
    """Resume uses only remaining allowances; it never grants a new arm budget."""
    from ForecastAgent.runtime import retrieval
    values = {k:getattr(retrieval,k) for k in ('COLLECTION_MAX_TURNS','COLLECTION_HTTP_PER_DISPATCH',
                                             'MODEL_FAILURES_PER_DISPATCH','MAX_RUN_SECONDS')}
    retrieval.COLLECTION_MAX_TURNS = max(0,BUDGET['model_decisions']-used_decisions)
    retrieval.COLLECTION_HTTP_PER_DISPATCH = max(0,BUDGET['model_http']-used_http)
    retrieval.MODEL_FAILURES_PER_DISPATCH = max(0,BUDGET['model_failures']-used_failures)
    retrieval.MAX_RUN_SECONDS = max(0,remaining_seconds if remaining_seconds is not None else BUDGET['seconds_per_arm'])
    try: yield
    finally:
        for k,v in values.items():setattr(retrieval,k,v)


def acquisition_summary(bundle):
    from ForecastAgent.readers.quality import body_diagnostics
    readable = {u:p for u,p in bundle['pages'].items() if body_diagnostics(p.get('content',''))['usable_text']}
    attempts = bundle.get('model_attempts', [])
    graph = (bundle.get('research_loop') or {}).get('current') or {}
    events = bundle.get('research_acquisition', {}).get('events', [])
    tokens = [a.get('usage',{}).get('total_tokens') for a in attempts if isinstance(a.get('usage'),dict)]
    post = bundle.get('post_supplement_review', {})
    return {'readable_pages':len(readable), 'readable_characters':sum(len(p['content']) for p in readable.values()),
            'saved_pages':len(bundle['pages']), 'excerpts':len(bundle.get('excerpts',[])),
            'super_http':len(attempts)+post.get('model_http_attempts',0),
            'post_supplement_http':post.get('model_http_attempts',0),
            'known_total_tokens':sum(t for t in tokens if type(t) is int)+post.get('known_total_tokens',0),
            'unknown_total_usage_attempts':len(attempts)-sum(type(t) is int for t in tokens)+post.get('unknown_usage_attempts',0),
            'tavily_basic':len(bundle.get('searches',[])), 'exa':len(bundle.get('exa_searches',[])),
            'initial_fetch_reservations':len(bundle.get('fetch_attempts',[])),
            'extract_batches':len(bundle.get('extract_attempts',[])),
            'map_revisions':bundle.get('research_loop',{}).get('revision',0),
            'map_nodes':len(graph.get('nodes',[])), 'map_relations':len(graph.get('relations',[])),
            'linked_network_actions':sum(bool(e['research_node_ids']) for e in events),
            'actions_after_map_update':sum(e['map_revision']>0 for e in events),
            'linked_actions_after_map_update':sum(e['map_revision']>0 and bool(e['research_node_ids']) for e in events),
            'unlinked_network_actions':sum(not e['research_node_ids'] for e in events),
            'linked_material_progress_actions':sum(bool(e['research_node_ids']) and any(p['usable_text'] for p in e['new_bodies']) for e in events),
            'pending_map_update':bundle.get('research_acquisition',{}).get('pending_map_update'),
            'gaps':bundle.get('result',{}).get('gaps') if isinstance(bundle.get('result'),dict) else None,
            'body_character_count_is_not_relevance':True, 'truth_verified':False}


def score(bundle, folder):
    question = bundle['request']; heads,spec = registry(question)
    common, selection = pack(bundle,heads)
    scoring = copy.deepcopy(common); map_audit = None
    if bundle.get('research_loop'):
        notes,map_audit = decision.scoring_map(bundle,common)
        if notes.get('factual_grounding_present'):
            scoring['research_map'] = notes
    from ForecastAgent.analysis.mercury_evidence_chain import request_bytes
    if request_bytes(scoring,heads)>decision.BYTE_CAP:
        scoring=copy.deepcopy(common)
        map_audit={'scoring_map_omitted':True,'reason':'Map exceeds preserved original-text scoring cap'}
    save(folder/'common.json',common);save(folder/'state.json',scoring)
    save(folder/'selection.json',selection);save(folder/'questions.json',heads)
    response=decision_http.call(scoring,folder/'decision',heads)
    if spec is None:raw={'probability_yes':response['answers']['event_yes']['noul']}
    else:
        distribution=forecast(response,spec)
        raw=({'probability_yes_per_category':distribution['probabilities']} if spec['kind']=='multiple_choice'
             else {'continuous_cdf':distribution['raw_cdf']})
    result={'status':'completed','raw_forecast':raw,'payload':payload(question,raw),'submitted':False,
            'analysis_policy':'Unchanged event-only Mercury scoring with frozen original evidence selection',
            'map_delivered':bool(scoring.get('research_map')),'map_audit':map_audit}
    result['summary']=live_trial.forecast_summary(result,question);save(folder/'result.json',result)
    return result


def provider_attention(folder):
    attention=[]
    paths=set(folder.rglob('model_calls/*.json')) | set(folder.rglob('decision/http/*.json')) | set(folder.rglob('post-supplement-map/model-http/*.json'))
    for p in sorted(paths):
        r=load(p)
        if r.get('http_status') in {401,402,403,429}:
            attention.append({'path':str(p),'http_status':r['http_status'],'status':r.get('status')})
    bundle=folder/'acquisition/collection/bundle.json'
    if bundle.exists():
        saved=load(bundle)
        for key in ('searches','exa_searches','extract_attempts'):
            for i,r in enumerate(saved.get(key,[])):
                if r.get('http_status') in {401,402,403,429}:
                    attention.append({'path':str(bundle),'ledger':key,'index':i,
                        'http_status':r['http_status'],'status':r.get('status')})
    return attention


def run(parents, root, *, execute=False, limit_cases=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if not 1<=len(parents)<=5:raise ValueError('Use one to five explicit paired cases')
    if limit_cases is not None and (type(limit_cases) is not int or not 1<=limit_cases<=len(parents)):
        raise ValueError('Case limit must be within the frozen input list')
    if os.environ.get('FORECAST_MODEL') != live_trial.SUPER or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER')!='0':
        raise ValueError('Freeze Super directly with automatic model routing disabled')
    old=load(root/'identity.json') if (root/'identity.json').exists() else None
    when=old['started_at_utc'] if old else datetime.now(timezone.utc).isoformat()
    inputs=[]
    for path in parents:
        bundle=load(path); ident=str(bundle['request']['id'])
        requests={a:input_for(bundle,when,a) for a in ('baseline','fusion')}
        inputs.append({'id':ident,'source':str(Path(path).resolve()),'sha256':live_trial.sha(path),'requests':requests})
    if len({e['id'] for e in inputs})!=len(inputs):raise ValueError('Duplicate question ID')
    identity={'schema':PROTOCOL,'started_at_utc':when,'inputs':inputs,'budget':BUDGET,
              'code':{a:pipeline.identity(inputs[0]['requests'][a],True)['candidate_sha256'] for a in ('baseline','fusion')},
              'harness_sha256':hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
              'model':live_trial.SUPER,'submitted':False,'old_quotas_reset':False,
              'comparison':'Same questions, provider and budgets. Live web responses can differ; no controlled accuracy claim.'}
    with task_lock(root):
        if old and old!=identity:raise ValueError('Frozen campaign input, code or budget changed')
        save(root/'identity.json',identity)
        rows=[];halt=None
        for entry in inputs[:limit_cases]:
            ident=entry['id'];row={'id':ident,'question':entry['requests']['baseline']['question'],'arms':{}}
            order=['baseline','fusion'] if int(ident)%2 else ['fusion','baseline']
            row['execution_order']=order
            for arm in order:
                folder=root/'cases'/ident/arm;request=entry['requests'][arm]
                save(folder/'input.json',request)
                if not execute:
                    row['arms'][arm]={'status':'prepared'};continue
                try:
                    path=folder/'acquisition/collection/bundle.json'
                    existing=load(path) if path.exists() else {}
                    attempts=existing.get('model_attempts',[])
                    decisions=sum(a.get('status')=='received' for a in attempts)
                    clock=folder/'collection-clock.json'
                    if not clock.exists():save(clock,{'started_at_utc':datetime.now(timezone.utc).isoformat()})
                    started=datetime.fromisoformat(load(clock)['started_at_utc'])
                    remaining=BUDGET['seconds_per_arm']-(datetime.now(timezone.utc)-started).total_seconds()
                    with bounded_dispatch(len(attempts),decisions,len(attempts)-decisions,remaining):
                        report=pipeline.run(request,folder/'acquisition',supplement_network=True)
                    package=folder/'acquisition/package.json'
                    bundle=load(package if package.exists() else path)
                    summary=acquisition_summary(bundle)
                    if summary['super_http']>BUDGET['model_http'] or summary['tavily_basic']>3 or summary['exa']>1 or summary['initial_fetch_reservations']>8:
                        raise ValueError('Frozen acquisition cap exceeded')
                    row['arms'][arm]={'status':'acquired' if package.exists() else 'preserved_incomplete',
                                      'summary':summary,'pipeline_state':report.get('state')}
                    if bundle.get('research_loop'):
                        save(folder/'graph.json',bundle['research_loop'].get('current'))
                        save(folder/'graph-audit.json',state.audit(bundle))
                    attention=provider_attention(folder)
                    if attention:
                        halt={'reason':'provider_attention_required','records':attention};row['arms'][arm]['attention']=attention
                    elif summary['readable_pages']:
                        try:
                            result=score(bundle,folder/'score')
                            row['arms'][arm]['score']={'status':'completed','summary':result['summary'],'map_delivered':result['map_delivered']}
                        except Exception as exc:
                            row['arms'][arm]['score']={'status':'failed','error':type(exc).__name__+': '+str(exc)}
                            attention=provider_attention(folder)
                            if attention:
                                halt={'reason':'provider_attention_required','records':attention}
                                row['arms'][arm]['attention']=attention
                    else:row['arms'][arm]['score']={'status':'no_readable_material','fabricated_forecast':False}
                except Exception as exc:
                    row['arms'][arm]={'status':'failed','error':type(exc).__name__+': '+str(exc)}
                    attention=provider_attention(folder)
                    if attention:
                        halt={'reason':'provider_attention_required','records':attention}
                        row['arms'][arm]['attention']=attention
                save(root/'cases'/ident/'result.json',row)
                print(json.dumps({'id':ident,'arm':arm,'status':row['arms'][arm]['status'],
                                  'summary':row['arms'][arm].get('summary')},ensure_ascii=False),flush=True)
                if halt:break
            rows.append(row)
            if any(live_trial.sha(e['source'])!=e['sha256'] for e in inputs):raise ValueError('Old snapshot changed')
            receipts=sorted(root.rglob('model_calls/*.json'))+sorted(root.rglob('decision/http/*.json'))
            report={'schema':PROTOCOL,'rows':rows,'requested':len(inputs),'processed':len(rows),'halt':halt,
                    'usage':live_trial.usage(receipts),'budget':BUDGET,'submitted':False,
                    'old_snapshots_preserved':True,'new_acquisition':True,'labels_read':False,
                    'accuracy':None,'brier':None,'same_model_and_budget':True,
                    'live_web_response_differences':True,'experimental_branch_only':True}
            save(root/'report.json',report)
            if halt:break
        return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parents',type=Path,required=True);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--limit-cases',type=int,help='Stop after the first frozen cases for smoke review; remaining cases keep their identity')
    p.add_argument('--execute',action='store_true');args=p.parse_args()
    run(load(args.parents),args.root,execute=args.execute,limit_cases=args.limit_cases)


if __name__=='__main__':main()
