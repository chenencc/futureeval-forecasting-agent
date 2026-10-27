"""Three finite historical arrivals, isolated HF state, no platform submissions."""
import argparse,contextlib,io,json,os,re,shutil,tempfile
from collections import Counter
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.contracts import now,digest
from pipeline_v2.intelligence_package import load
from pipeline_v2.routing import configure_credentials
from ForecastAgent.competition import platform
from ForecastAgent.competition.queue import questions,descriptor
from .runner import Runner,initial_state,blind_post
from .platform_adapter import live_request
from .storage import Store,RuntimeCheckpoint,dumps,sha,safe
from .products import verify_context,prefix,bundle
from .collect import collect
from .analyze import analyze

CASES=(('46205','46432'),('46204','46431'),('46203','46430'))
KEY='metaculus/_control/checkpoint.json'

class DebugStore:
    """Map EVERY logical product/checkpoint/object into one debug namespace."""
    def __init__(self,backend,session):
        if not re.fullmatch('[0-9a-f]{32}',session):raise ValueError('DEBUG_SESSION_ID')
        self.backend=backend;self.base='metaculus/_debug/'+session;self.guards={}
    def physical(self,key):return self.base+'/'+safe(key)[len('metaculus/'):]
    @property
    def head(self):return self.backend.head
    def exists(self,key):return self.backend.exists(self.physical(key))
    def read(self,key,revision=None):return self.backend.read(self.physical(key),revision)
    def publish(self,updates):
        mapped={self.physical(k):v for k,v in updates.items()}
        self.backend.guards={self.physical(k):v for k,v in self.guards.items()}
        result=self.backend.publish(mapped)
        for k in self.guards:
            if k in updates:self.guards[k]=updates[k]
        return result

class HistoricalClient:
    """Eligibility/own-forecast simulation only; preserve original model input."""
    def __init__(self,native,post_id,question_id):
        self.native,self.post_id,self.question_id=native,str(post_id),str(question_id)
        self.until=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
        self.original=None;self.original_descriptor=None
    def account(self):return self.native.account()
    def post(self,ident):
        if str(ident)!=self.post_id:raise ValueError('DEBUG_POST_ALLOWLIST')
        raw=self.native.post(ident)
        matches=[q for q in questions(raw) if str(q['id'])==self.question_id]
        if len(matches)!=1:raise ValueError('DEBUG_QUESTION_IDENTITY')
        q=matches[0];self.original=blind_post(raw,q);self.original_descriptor=descriptor(raw,q)
        view=deepcopy(self.original);view.update(status='open',user_permission='forecaster')
        view['question']['status']='open';view['question']['my_forecasts']=dict(latest=None,history=[])
        for k in ('actual_close_time','close_time','scheduled_close_time','spot_scoring_time'):view['question'][k]=self.until
        return view
    def request(self,*args,**kwargs):raise RuntimeError('DEBUG_SUBMISSION_FORBIDDEN')

class SimulatedSubmission:
    """In-memory API sink for the native delivery serializer/readback; no HTTP."""
    def __init__(self,view,task,checkpoint):self.view=deepcopy(view);self.task=task;self.checkpoint=checkpoint;self.posts=0;self.comment=None
    def post(self,ident):
        if str(ident)!=self.task['post_id']:raise RuntimeError('SIMULATED_POST_IDENTITY')
        return self.view
    def comments(self,ident):
        return [] if self.comment is None else [dict(id=1,text=self.comment,author={'id':platform.BOT_ID},is_private=True)]
    def request(self,method,path,body):
        if method!='POST' or path!='questions/bulk-forecast-comment/' or body['user_id']!=platform.BOT_ID:raise RuntimeError('SIMULATED_ENDPOINT')
        # A durable reserved intent must precede even this in-memory POST.
        state=json.loads(self.checkpoint.store.read(self.checkpoint.key))
        name='tasks/'+self.task['id']+'/simulation-submission/submission.json';hashed=state['runtime'].get(name)
        if not hashed:raise RuntimeError('SIMULATED_INTENT_NOT_DURABLE')
        intent=json.loads(self.checkpoint.store.read('metaculus/_control/objects/'+hashed))
        if intent['status']!='reserved':raise RuntimeError('SIMULATED_INTENT_NOT_RESERVED')
        forecast=body['forecasts'][0];q=self.view['question']
        if 'probability_yes' in forecast:values=[1-forecast['probability_yes'],forecast['probability_yes']]
        elif 'continuous_cdf' in forecast:values=forecast['continuous_cdf']
        else:values=[forecast['probability_yes_per_category'].get(o) for o in q['options']]
        q['my_forecasts']=dict(latest=dict(author_id=platform.BOT_ID,forecast_values=values,start_time=now()),history=[])
        self.comment=body['comments'][0]['text'];self.posts+=1
        return dict(status=201)

def run_case(backend,root,session,index,native,*,collector=collect,analyzer=analyze):
    if type(index)!=int or not 1<=index<=3:raise ValueError('DEBUG_CASE_INDEX')
    root=Path(root);store=DebugStore(backend,session);report_key=f'metaculus/case-{index}/audit.json'
    post_id,qid=CASES[index-1];client=HistoricalClient(native,post_id,qid)
    cp=RuntimeCheckpoint(store,root/'writer',key=KEY);state=None
    if index==1:
        if store.exists(KEY):raise RuntimeError('DEBUG_SESSION_ALREADY_STARTED')
        # Carry known historical consumption as a floor; never reset per question.
        production=json.loads(backend.read(KEY));budget_name='budgets/control/provider-budget.jsonl'
        hashed=production.get('runtime',{}).get(budget_name)
        if not hashed:raise RuntimeError('PRODUCTION_BUDGET_FLOOR_REQUIRED')
        raw=backend.read('metaculus/_control/objects/'+hashed)
        if sha(raw)!=hashed:raise RuntimeError('PRODUCTION_BUDGET_HASH')
        budget=json.loads(raw)
        if not isinstance(budget.get('days'),dict):raise RuntimeError('PRODUCTION_BUDGET_SCHEMA')
        path=cp.root/budget_name;path.parent.mkdir(parents=True);path.write_bytes(raw)
        state=initial_state('debug-three-'+session)
        state['debug']=dict(session=session,cases=[dict(post_id=p,question_id=q) for p,q in CASES],completed=[],test_only=True,
            budget_seed_sha256=hashed,production_checkpoint_sha256=digest(production),started_at=now())
        store.guards[KEY]=None;cp.save(state)
    else:
        state=cp.restore()
        if state.get('debug',{}).get('completed')!=list(range(1,index)):raise RuntimeError('DEBUG_PREDECESSOR_NOT_PASSED')
        prior=json.loads(store.read(f'metaculus/case-{index-1}/audit.json'))
        if prior.get('status')!='debug_case_passed':raise RuntimeError('DEBUG_PREDECESSOR_NOT_PASSED')
    try:
        baseline=json.loads((cp.root/'budgets/control/provider-budget.jsonl').read_bytes())
        def analyzed(value,folder,checkpoint):
            result=analyzer(value,folder,checkpoint)
            return {**result,'test_only':True,'run_purpose':'historical-listener-simulation'}
        runner=Runner(cp,client,writer=state['writer'],live=False,limit=1,collector=collector,analyzer=analyzed)
        os.environ['FORECAST_V2_ONCE_NAMESPACE']='metaculus-debug-'+session
        simulated=client.post(post_id);runner.ingest([simulated]);task=runner.state['tasks'].get(qid)
        if not task or task['status']!='discovered':raise RuntimeError('DEBUG_ADMISSION_FAILED')
        task.update(test_only=True,run_purpose='historical-listener-simulation',post=client.original,
            request=live_request(client.original,client.original['question']))
        simulation=dict(test_only=True,original_descriptor=client.original_descriptor,simulated_deadline=client.until,
            admission_status_and_own_history_simulated=True,model_input_original_clocks_preserved=True,
            temporal_policy='current-evidence-for-historical-question-not-historical-asof',new_opening_claimed=False)
        runner.checkpoint({f'metaculus/case-{index}/simulation.json':dumps(simulation)})
        with patch.object(platform,'deliver',side_effect=RuntimeError('DEBUG_SUBMISSION_FORBIDDEN')):
            outcome=runner.run([])
        if outcome['failures'] or task['status']!='analyzed':raise RuntimeError('DEBUG_CASE_PIPELINE_FAILED')
        sink=SimulatedSubmission(simulated,task,cp);candidate=task['candidate'];native_save=platform.save
        def durable(path,value):native_save(path,value);runner.checkpoint()
        folder=cp.root/'tasks'/qid/'simulation-submission'
        with patch.object(platform,'save',durable):
            simulated_receipt=platform.deliver(sink,task,candidate['payload'],candidate['comment'],folder,enabled=True)
            repeated=platform.deliver(sink,task,candidate['payload'],candidate['comment'],folder,enabled=True)
        if simulated_receipt['status']!='accepted' or repeated['status']!='accepted' or sink.posts!=1:raise RuntimeError('SIMULATED_SUBMISSION_READBACK')
        runner.checkpoint({f'metaculus/case-{index}/submission-simulation.json':dumps(dict(simulation_only=True,actual_platform_posts=0,simulated_posts=sink.posts,receipt=simulated_receipt))})
        pre=prefix(task);context_raw=store.read(task['context_key'],task['context_revision'])
        analysis_raw=store.read(task['analysis_key'],task['analysis_revision'])
        manifest=json.loads(store.read(pre+'/manifest.json',task['analysis_revision']))
        for name,raw in [('context.parquet',context_raw),('analysis.parquet',analysis_raw)]:
            if manifest['files'][name]['sha256']!=sha(raw):raise RuntimeError('DEBUG_MANIFEST_HASH')
        for name,raw in [('context',context_raw),('analysis',analysis_raw)]:
            path=root/(name+'.parquet');path.write_bytes(raw)
        value=load(root/'context.parquet');result=load(root/'analysis.parquet');snapshot=verify_context(value)
        if not value['test_only'] or not result['test_only'] or result['snapshot_id']!=value['snapshot_id'] or result['context_sha256']!=digest(value) or result['run_id']!=task['run_id'] or result['question_id']!=value['question_id']:raise RuntimeError('DEBUG_ANALYSIS_BINDING')
        # Fresh root restores actual capture/analysis files and exact budget state.
        second=RuntimeCheckpoint(store,root/'reader',key=KEY);restored=second.restore()
        if restored['tasks'][qid]['status']!='analyzed':raise RuntimeError('DEBUG_RESTART_STATUS')
        ledger=Path('budgets/control/provider-budget.jsonl')
        before=(second.root/ledger).read_bytes()
        duplicate=Runner(second,client,writer=state['writer'],live=False,limit=1,collector=collector,analyzer=analyzed)
        if duplicate.run([client.post(post_id)])['processed']!=0 or (second.root/ledger).read_bytes()!=before:raise RuntimeError('DEBUG_DUPLICATE_RECOLLECTED')
        runner.retire(task,cp.root/'tasks'/qid)
        shutil.rmtree(second.root)
        for name in ['context.parquet','analysis.parquet']:(root/name).unlink()
        runner.state['debug']['completed'].append(index);runner.checkpoint()
        final=RuntimeCheckpoint(store,root/'after-retirement',key=KEY);final_state=final.restore()
        if any((final.root/'tasks').rglob('*')) or not (final.root/ledger).is_file():raise RuntimeError('DEBUG_RETIREMENT_OR_BUDGET_RESTORE')
        raw_budget=json.loads((final.root/ledger).read_bytes())
        source=bundle(value)
        consumption={kind:sum(v.get(kind,0) for v in raw_budget['days'].values())-sum(v.get(kind,0) for v in baseline['days'].values()) for kind in ['model','search','fetch']}
        report=dict(status='debug_case_passed',session=session,case_index=index,question_id=qid,post_id=post_id,
            question_title=value['request'].get('question'),question_type=value['request'].get('question_type'),test_only=True,
            hf_prefix=store.physical(pre),snapshot_id=value['snapshot_id'],context_sha256=sha(context_raw),analysis_sha256=sha(analysis_raw),
            products_revision=task['analysis_revision'],exact_revision_readback=True,manifest_verified=True,analysis_binding_verified=True,
            restart_restored=True,duplicate_discovery_processed=0,duplicate_discovery_no_acquisition_calls=True,budget_restored=True,
            budget_days=raw_budget['days'],material_count=len(snapshot['materials']),evidence_gaps=snapshot['gaps'],
            analysis_input_pages=len(source['pages']),analysis_input_characters=sum(len(p['content']) for p in source['pages'].values()),
            unverified_reading_sources=len(source['original_reading'].get('unverified_source_ids',[])),
            source_http_status_counts=dict(Counter(str(m.get('source',{}).get('http_status')) for m in snapshot['materials'])),
            provider_budget_increment=consumption,
            local_payloads_retired=True,completed_cases=final_state['debug']['completed'],submissions=0,new_opening_claimed=False,
            simulated_submission_readback=True,simulated_duplicate_posts=0,actual_submission_acceptance_claimed=False,
            historical_asof_claimed=False,finished_at=now())
        store.publish({report_key:dumps(report)})
        return report
    except Exception as exc:
        # No next case may run without a durable passed receipt.
        latest=cp.state or state or {};task=latest.get('tasks',{}).get(qid,{})
        code=task.get('last_error') or getattr(exc,'code',None) or (str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{1,99}',str(exc)) else type(exc).__name__)
        report=dict(status='debug_stopped_on_failure',session=session,case_index=index,question_id=qid,post_id=post_id,
            stage=task.get('status'),code=str(code) if re.fullmatch('[A-Za-z0-9_]{1,100}',str(code)) else 'INTERNAL_ERROR',
            hf_prefix=store.base,payloads_preserved=True,later_cases_started=False,submissions=0,finished_at=now())
        store.publish({report_key:dumps(report)})
        return report

def public(report):
    keys=('status','session','case_index','question_id','post_id','question_title','question_type','stage','code','hf_prefix','snapshot_id',
          'products_revision','exact_revision_readback','restart_restored','duplicate_discovery_processed','budget_restored','material_count',
          'local_payloads_retired','completed_cases','submissions','simulated_submission_readback','simulated_duplicate_posts',
          'analysis_input_pages','analysis_input_characters','unverified_reading_sources','source_http_status_counts','provider_budget_increment','finished_at')
    return {k:report[k] for k in keys if k in report}

def diagnostics(backend,session,index):
    store=DebugStore(backend,session)
    if not store.exists(KEY):return dict(status='debug_diagnostics',session=session,case_index=index,checkpoint_exists=False)
    state=json.loads(store.read(KEY));qid=CASES[index-1][1];task=state['tasks'].get(qid,{})
    result=dict(status='debug_diagnostics',session=session,case_index=index,checkpoint_exists=True,task_status=task.get('status'),
        last_error=task.get('last_error'),capture_attempts=task.get('attempts',{}).get('capture',0),analysis_attempts=task.get('attempts',{}).get('analysis',0),
        active_runtime_files=len(state.get('runtime',{})),completed_cases=state.get('debug',{}).get('completed',[]),capture_stages=[])
    for name,hashed in state.get('runtime',{}).items():
        if not name.endswith('/capture/metaculus/'+qid+'/state.json'):continue
        raw=json.loads(store.read('metaculus/_control/objects/'+hashed));control=raw.get('control',{})
        for run in control.get('runs',[]):
            if run.get('run_id')!=task.get('capture_run_id'):continue
            for stage,attempts in run.get('stages',{}).items():
                for attempt in attempts:
                    result['capture_stages'].append({k:attempt[k] for k in ['status','started_at','finished_at','error_code','usage'] if k in attempt}|dict(stage=stage))
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--case-index',type=int,required=True);p.add_argument('--inspect',action='store_true');args=p.parse_args()
    if not 1<=args.case_index<=3:raise ValueError('DEBUG_CASE_INDEX')
    backend=Store(os.environ.get('HF_DATASET','chchen13/forecastagent-data'),os.environ['HF_TOKEN'])
    if args.inspect:return diagnostics(backend,args.session,args.case_index)
    configure_credentials()
    native=platform.Client(os.environ['METACULUS_TOKEN'])
    original=native.request
    def readonly(method,path,data=None):
        if method!='GET':raise RuntimeError('DEBUG_SUBMISSION_FORBIDDEN')
        return original(method,path,data)
    with tempfile.TemporaryDirectory(prefix='metaculus-three-debug-') as tmp,patch.object(native,'request',readonly):
        return run_case(backend,Path(tmp),args.session,args.case_index,native)

if __name__=='__main__':
    try:
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):result=main()
        print(json.dumps(result if result['status']=='debug_diagnostics' else public(result)))
        if result['status'] not in {'debug_case_passed','debug_diagnostics'}:raise SystemExit(1)
    except Exception as exc:
        code=getattr(exc,'code',type(exc).__name__);print(json.dumps(dict(status='debug_blocked',code=code if re.fullmatch('[A-Za-z0-9_]{1,100}',str(code)) else 'INTERNAL_ERROR')));raise SystemExit(1)
