"""Saved-material feedback verification; collection ledgers remain immutable."""
import argparse
import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from ForecastAgent.analysis.pilot import Journal, digest, load, save
from ForecastAgent.providers.model import ask_model
from ForecastAgent.research_loop import fusion, runtime, state, delta, delivery, reading_views, live_trial, gap_feedback
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.context import collection_context
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.runtime.task_lock import task_lock

CAP=4
LOCAL={'inspect_research_state','update_research_state'}
QUOTAS=('searches','exa_searches','fetch_attempts','extract_attempts','model_attempts','acquisition_limits','update_attempts','sessions')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fork(source, root, gap_policy=None):
    original=load(source); child=copy.deepcopy(original)
    request=child['request']
    request.update({reading_views.FIELD:reading_views.POLICY,delta.FIELD:delta.POLICY,delivery.FIELD:delivery.POLICY})
    if gap_policy:
        request[gap_feedback.FIELD]=gap_policy
    child['request_hash']=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    child['verification_origin']={'bundle_sha256':sha(source),'source':str(source),
        'preserved_result':copy.deepcopy(child.get('result')),
        'scope':'Isolated saved-material verification, not reopening a collection task.'}
    child['result']=None
    child['control']['forced_close']=False
    child['research_acquisition']['pending_map_update']=True
    child['messages']=[]  # Historical requests/replies stay in the frozen input.
    held={}
    if not child['research_loop']['revision']:
        urls=sorted(child['pages']);held={u:child['pages'].pop(u) for u in urls[1:]}
    save(root/'frozen-input.json',original)
    save(root/'held-saved-pages.json',held)
    save(root/'collection/bundle.json',child)
    return original,held


def entry(source, folder, execute, http_cap=CAP, gap_policy=None):
    source=Path(source);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    if (folder/'result.json').exists():return load(folder/'result.json')
    if not (folder/'frozen-input.json').exists():fork(source,folder,gap_policy)
    original=load(folder/'frozen-input.json');held=load(folder/'held-saved-pages.json')
    if digest(original)!=digest(load(source)):raise ValueError('Frozen source changed')
    child=load(folder/'collection/bundle.json');task=RetrievalTask(folder/'collection',child['request'])
    initial=original['research_loop']['revision'];history=load(folder/'steps.json') if (folder/'steps.json').exists() else []
    old_nodes=(original['research_loop'].get('current') or {}).get('nodes',[])
    old_urls={r['url'] for n in old_nodes for r in n.get('bindings',[])}
    # A new map must bind stage-two material, not merely its bootstrap source.
    expected_urls=set(sorted(original['pages'])[1:]) if not initial else set(original['pages'])-old_urls
    journal=Journal(folder/'model-http',http_cap)
    stopped='prepared';observed=[]
    if execute:
        while len(list((folder/'model-http').glob('*.json'))) < http_cap:
            revision=task.bundle['research_loop']['revision']
            # Stage two promotes captured originals from the same input snapshot.
            # It is a local read, never a new provider acquisition or quota reset.
            if not initial and revision==1 and held:
                task.bundle['pages'].update(held);held={}
                task.bundle['research_acquisition']['pending_map_update']=True
                save(folder/'held-saved-pages.json',held);task.save()
                save(folder/'promotion.json',{'scope':'Preserved saved sources only',
                    'promoted_at_utc':datetime.now(timezone.utc).isoformat(),
                    'source_page_hashes':{u:digest(p) for u,p in task.bundle['pages'].items()},
                    'quota_changed':False})
            forced='update_research_state' if fusion.inspection_ready(task) else 'inspect_research_state'
            tools=active_tools(task,runtime.configure(task,[]),forced)
            if not tools:stopped='local_tool_unavailable';break
            messages=collection_context(task,forced_tool=forced)
            if gap_policy:
                frame=json.loads(messages[1]['content'])
                frame['available_tools']=[forced]
                frame['next_action']={'tool':forced,'instruction':'Use the native bounded read/update cycle. Process newly read source versions with explicit receipts; record deferred material honestly. No acquisition or scoring is authorized in this verification.'}
                messages[1]['content']=json.dumps(frame,ensure_ascii=False)
            if forced=='inspect_research_state':
                guidance={'goal':'Inspect substantive saved material for a NEW source-bound observation; prefer target-period baseline or missing driver, not an empty landing page. Unpublished future results remain unknown.',
                    'unbound_saved_urls':sorted(set(task.bundle['pages'])-{r['url'] for n in (task.bundle['research_loop'].get('current') or {}).get('nodes',[]) for r in n.get('bindings',[])}),
                    'limit':2,'network_allowed':False,
                    'navigation':'Use an exact url and optionally query/offset. source_offset changes metadata only. Read target-relevant body lines, then copy a short exact literal quote into the map. Never fabricate a result.'}
                messages.append({'role':'user','content':json.dumps(guidance)})
            try:
                message=ask_model(messages,os.environ['OPENROUTER_API_KEY'],tools=tools,forced_tool=forced,
                    observer=journal,deadline=time.monotonic()+180,**fusion.model_options(task,'update_research_state' if gap_policy else forced))
            except Exception as exc:
                stopped='model_request_failure'
                save(folder/'provider-failure.json',{'type':type(exc).__name__,'message':str(exc)[:800]})
                break
            calls=message.get('tool_calls') or [];task.bundle['messages'].append(message)
            if len(calls)!=1:
                stopped='expected_one_local_tool_call';task.save();break
            call=calls[0];name=call['function']['name'];row={'name':name,'revision_before':revision}
            try:
                args=json.loads(call['function']['arguments']);row['arguments']=args
                if name not in LOCAL or forced and name!=forced:raise ValueError('Only the currently declared local tool is authorized')
                # The native envelope isolates bad nodes; validating every child
                # against the presentation schema here would reject good nodes too.
                runtime.validate_args(task,name,args,tools)
                result=task.execute(name,args,'saved-verification-'+str(len(history)))
                row.update(ok=True,material_sha256=result.get('material_sha256'),revision_after=task.bundle['research_loop']['revision'])
                if name=='inspect_research_state':
                    row['delivered_evidence']=[{k:s[k] for k in ('url','body_sha256','evidence_id','start','end','coordinate_space') if k in s} for s in result['evidence']]
                else:
                    row['acceptance']=result.get('acceptance');row['new_binding_urls']=sorted({r['url'] for n in task.bundle['research_loop']['current']['nodes'] for r in n.get('bindings',[])}-old_urls)
                    if gap_policy:row['gap_feedback']=result.get('gap_feedback')
            except (ValueError,KeyError,TypeError) as exc:
                result={'error':str(exc),'contract_error':getattr(exc,'details',None),'acceptance':getattr(exc,'report',None)}
                row.update(ok=False,error=str(exc))
            task.bundle['messages'].append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
            history.append(row);save(folder/'steps.json',history);task.save()
            current=task.bundle['research_loop'].get('current') or {}
            observed=sorted({r['url'] for n in current.get('nodes',[]) if n['kind']=='observation' for r in n['bindings']} & expected_urls)
            required_revision=initial+1 if initial else 2
            reviewed_new=any(r['disposition']=='incorporated' and r['source']['url'] in expected_urls
                for e in task.bundle.get('research_gap_feedback',{}).get('events',[]) for r in e['accepted_reviews'])
            if task.bundle['research_loop']['revision']>=required_revision and observed and (not gap_policy or reviewed_new):
                stopped='new_saved_evidence_bound_in_later_map';break
        else:stopped='verification_http_cap'
    for key in QUOTAS:
        if task.bundle.get(key)!=original.get(key):raise ValueError('Original collection ledger changed: '+key)
    for url,page in task.bundle['pages'].items():
        if page!=original['pages'][url]:raise ValueError('Original page changed')
    audit=state.audit(copy.deepcopy(task.bundle))
    result={'id':str(original['request']['id']),'status':stopped,'initial_revision':initial,
        'final_revision':task.bundle['research_loop']['revision'],'new_observation_binding_urls':observed,
        'required_new_source_urls':sorted(expected_urls),
        'map_audit':audit,'historical_collection_budgets_preserved':True,'original_pages_preserved':True,
        'usage':live_trial.usage(sorted((folder/'model-http').glob('*.json'))),
        'new_search_calls':0,'new_source_http_calls':0,'mercury_calls':0,'scores_generated':False,
        'submission':False,'scope':'Saved-material feedback only; not a fresh live collection acceptance.'}
    if gap_policy:
        result['gap_feedback']=gap_feedback.view(task)
        result['receipt_dispositions']=[{'material_id':r['material_id'],'url':r['source']['url'],
            'disposition':r['disposition'],'effect':r['effect'],'reason':r['reason']}
            for e in task.bundle['research_gap_feedback']['events'] for r in e['accepted_reviews']]
    if execute:save(folder/'result.json',result)
    return result


def run(sources,root,execute=False,http_cap=CAP,gap_policy=None):
    if not isinstance(http_cap,int) or not 1<=http_cap<=CAP:raise ValueError('Verification cap must be 1..4')
    if gap_policy not in {None,gap_feedback.POLICY}:raise ValueError('Unknown gap-feedback verification policy')
    if os.environ.get('FORECAST_MODEL')!=live_trial.SUPER or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER')!='0':
        raise ValueError('Freeze Super directly')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    identity={'scope':'saved-material native local tools only','sources':{str(p):sha(p) for p in sources},
        'per_case_http_cap':http_cap,'new_search_cap':0,'new_fetch_cap':0,'model':live_trial.SUPER,
        'code_sha256':{str(p.relative_to(Path(__file__).resolve().parents[2])):
            hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
            for p in sorted(Path(__file__).resolve().parents[1].rglob('*.py')) if 'tests' not in p.parts}}
    if gap_policy:identity['gap_policy']=gap_policy
    if (root/'identity.json').exists() and load(root/'identity.json')!=identity:raise ValueError('Snapshot identity changed')
    save(root/'identity.json',identity)
    with task_lock(root):
        rows=[]
        for source in sources:
            row=entry(source,root/'cases'/str(load(source)['request']['id']),execute,http_cap,gap_policy)
            rows.append(row)
            print(json.dumps({'id':row['id'],'status':row['status'],'revision':row['final_revision']},ensure_ascii=False),flush=True)
    report={'rows':rows,'loop_passed':sum(r['status']=='new_saved_evidence_bound_in_later_map' for r in rows),
        'usage':live_trial.usage(sorted(root.rglob('model-http/*.json'))),'scope':identity['scope']}
    save(root/'report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--sources',type=Path,nargs='+',required=True);parser.add_argument('--execute',action='store_true')
    parser.add_argument('--per-case-http-cap',type=int,choices=range(1,CAP+1),default=CAP)
    parser.add_argument('--gap-feedback',action='store_true')
    args=parser.parse_args()
    from ForecastAgent.research_loop import gap_feedback
    report=run(args.sources,args.root,args.execute,args.per_case_http_cap,gap_feedback.POLICY if args.gap_feedback else None)
    print(json.dumps({'loops':report['loop_passed'],'rows':[{'id':r['id'],'status':r['status'],'revision':r['final_revision']} for r in report['rows']],
        'usage':report['usage']['totals']},ensure_ascii=False),flush=True)
