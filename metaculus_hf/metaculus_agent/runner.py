"""Bounded, single-writer orchestration. Live is fail-closed until migration."""
import argparse,contextlib,io,json,os,re,shutil,time,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.contracts import now,digest
from pipeline_v2.routing import configure_credentials
from ForecastAgent.competition import platform
from .platform_adapter import live_request,rule_identity,current
from ForecastAgent.competition.queue import descriptor,questions,utc
from ForecastAgent.monitor_tournament import collect_questions
from .storage import Store,QuestionBatch,RuntimeCheckpoint,dumps,Conflict,PersistenceError
from .products import context,product,prefix,manifest
from .collect import collect
from .analyze import analyze

TERMINAL={'accepted','already_forecasted','closed','deadline_missed','legacy_terminal','ambiguous_submission','collection_exhausted','analysis_exhausted','blocked_integrity'}
def initial_state(writer):return dict(schema='metaculus-checkpoint/1',writer=writer,migration_complete=False,tasks={},runtime={},updated_at=now())
def blind_post(post,question):
    """No community forecasts, outcome labels or other users' comments."""
    parent={k:post.get(k) for k in ('id','title','description','resolution_criteria','fine_print','created_at','status','user_permission')}
    keys=('id','title','description','resolution_criteria','fine_print','type','status','options','scaling','forecast_grid','inbound_outcome_count','open_lower_bound','open_upper_bound','unit','open_time','close_time','actual_close_time','scheduled_close_time','spot_scoring_time','scheduled_resolve_time')
    return {**parent,'question':{k:question[k] for k in keys if k in question}}
def eligible(post,q):
    desc=descriptor(post,q)
    return (q.get('status')=='open' and post.get('user_permission') in ('forecaster','curator','admin','creator') and
        q.get('type') in {'binary','multiple_choice','numeric','discrete','date'} and desc['deadline_utc'] and utc(desc['deadline_utc'])>utc())

class Runner:
    def __init__(self,checkpoint,client,*,writer,live=False,limit=1,seconds=3000,collector=collect,analyzer=analyze):
        self.cp,self.client=checkpoint,client;self.root=checkpoint.root;self.writer,self.live=writer,live
        self.limit,self.until=limit,time.monotonic()+seconds;self.collector,self.analyzer=collector,analyzer
        self.retirement_roots=[]
        self.state=checkpoint.restore()
        if self.state['writer']!=writer:raise RuntimeError('WRITER_OWNER_MISMATCH')
        if live and self.state.get('migration_complete') is not True:raise RuntimeError('SERVER_HANDOFF_REQUIRED')
        os.environ['FORECAST_V2_SHARED_BUDGET_ROOT']=str(self.root/'budgets')
        os.environ['FORECAST_V2_ONCE_NAMESPACE']='metaculus-public-v1'
        os.environ.setdefault('FORECAST_MODEL','nvidia/nemotron-3.5-super-vl:free')
    def checkpoint(self,extra=None):
        self.state['updated_at']=now();return self.cp.save(self.state,extra=extra)
    def ingest(self,posts):
        changed=False
        for post in posts:
            for q in questions(post):
                ident=str(q['id'])
                if ident in self.state['tasks'] or not eligible(post,q):continue
                # Authenticated existing-forecast check precedes any paid collection.
                actual=self.client.post(post['id']);selected=next(x for x in questions(actual) if str(x['id'])==ident)
                if not eligible(actual,selected):continue
                if platform.has_existing_forecast(selected):
                    self.state['tasks'][ident]=dict(id=ident,post_id=str(post['id']),status='already_forecasted');changed=True;continue
                p=blind_post(actual,selected);desc=descriptor(actual,selected)
                task=dict(id=ident,post_id=str(post['id']),status='discovered',run_id=uuid.uuid4().hex,
                    discovered_at=now(),deadline_utc=desc['deadline_utc'],rule_identity=rule_identity(actual,selected),
                    post=p,request=live_request(p,p['question']),attempts={})
                self.state['tasks'][ident]=task;changed=True
        if changed:self.checkpoint()
    def process(self,task):
        if getattr(self.cp.store,'batched',False):self.cp.store.check_owner()
        root=self.root/'tasks'/task['id'];root.mkdir(parents=True,exist_ok=True)
        ident=task['id'];status=task['status']
        pending_receipt=root/'submission'/'submission.json'
        if pending_receipt.exists() and self.live:
            # An existing intent is reconciled even after the deadline/closure.
            candidate=task['candidate']
            receipt=platform.deliver(self.client,task,candidate['payload'],candidate['comment'],root/'submission',enabled=True)
            task['status']=receipt['status'];self.checkpoint({prefix(task)+'/submission.json':dumps(receipt)})
            if receipt['status']=='accepted':self.retire(task,root)
            return
        if utc(task['deadline_utc'])<=utc():task['status']='deadline_missed';self.checkpoint();return
        # Current rules and permissions must remain valid for resumed tasks too.
        _,latest=current(self.client,task)
        if platform.has_existing_forecast(latest):task['status']='already_forecasted';self.checkpoint();return
        if status in {'discovered','capturing'}:
            if task['attempts'].get('capture',0)>=3:raise RuntimeError('COLLECTION_ATTEMPTS_EXHAUSTED')
            task['attempts']['capture']=task['attempts'].get('capture',0)+1
            task.setdefault('started_at',now());task['status']='capturing';self.checkpoint()
            package=self.collector(task,task['post'],root,self.checkpoint)
            from .raw_search import saved as saved_searches
            value=context(task,package,task['snapshot_id'],raw_search_responses=saved_searches(root));raw=product('context',value)
            task['context_key']=prefix(task)+'/context.parquet';task['context_hash']=digest(value)
            task['status']='sealed';task['context_bytes_sha256']=__import__('hashlib').sha256(raw).hexdigest()
            task['context_revision']=self.checkpoint({task['context_key']:raw,prefix(task)+'/manifest.json':dumps(manifest(task,{'context.parquet':raw}))})['revision']
        if task['status'] in {'sealed','analyzing'}:
            if task['attempts'].get('analysis',0)>=3:raise RuntimeError('ANALYSIS_ATTEMPTS_EXHAUSTED')
            raw=self.cp.store.read(task['context_key'],task.get('context_revision'))
            if __import__('hashlib').sha256(raw).hexdigest()!=task['context_bytes_sha256']:raise RuntimeError('RESTORED_CONTEXT_HASH')
            import pyarrow as pa,pyarrow.parquet as pq
            value=json.loads(pq.read_table(pa.BufferReader(raw)).to_pylist()[0]['payload'])
            if digest(value)!=task['context_hash']:raise RuntimeError('RESTORED_CONTEXT_BINDING')
            task['status']='analyzing';task['attempts']['analysis']=task['attempts'].get('analysis',0)+1;self.checkpoint()
            task['analysis_executions']=task.get('analysis_executions',0)+1;self.checkpoint()
            result=self.analyzer(value,root/'analysis'/('attempt-'+str(task['analysis_executions']).zfill(3)),self.checkpoint)
            if result['snapshot_id']!=task['snapshot_id'] or result['context_sha256']!=task['context_hash']:raise RuntimeError('ANALYSIS_CONTEXT_MISMATCH')
            candidate=result['candidate'];analysis_raw=product('analysis',result)
            task.update(status='analyzed',candidate=candidate,analysis_key=prefix(task)+'/analysis.parquet',analysis_hash=digest(result))
            for k in ['last_error','analysis_retry_at','provider_failure_category','operator_action_required']:task.pop(k,None)
            task['analysis_revision']=self.checkpoint({task['analysis_key']:analysis_raw,prefix(task)+'/manifest.json':dumps(manifest(task,{'context.parquet':raw,'analysis.parquet':analysis_raw}))})['revision']
        if task['status'] in {'analyzed','submitting'} and self.live:
            if getattr(self.cp.store,'batched',False):self.cp.store.check_owner()
            current(self.client,task);candidate=task['candidate'];task['status']='submitting';self.checkpoint()
            save=platform.save
            def durable_save(path,value):save(path,value);self.checkpoint()
            with patch.object(platform,'save',durable_save):
                receipt=platform.deliver(self.client,task,candidate['payload'],candidate['comment'],root/'submission',enabled=True)
            task['status']=receipt['status'] if receipt['status'] in {'accepted','already_forecasted'} else 'ambiguous_submission'
            self.checkpoint({prefix(task)+'/submission.json':dumps(receipt)})
        if task['status']=='accepted':
            # All content has exact cloud readback; retain budgets outside task payloads.
            self.retire(task,root)
    def retire(self,task,root):
        self.checkpoint();target=root.resolve()
        if target.parent!=(self.root/'tasks').resolve() or root.is_symlink():raise RuntimeError('UNSAFE_RETIREMENT')
        if getattr(self.cp.store,'batched',False):
            owned={k:v for k,v in self.cp.state['runtime'].items() if k.startswith('tasks/'+task['id']+'/')}
            key=prefix(task)+'/question-runtime.json'
            task.update(payloads_archived=True,runtime_archive_key=key)
            task.pop('post',None);task.pop('candidate',None)
            self.checkpoint({key:dumps(dict(schema='metaculus-question-runtime/1',question_id=task['id'],runtime=owned))})
            self.retirement_roots.append(target)
            return
        shutil.rmtree(target);task.pop('post',None);task.pop('candidate',None);self.checkpoint()
    def flush_question(self):
        self.checkpoint();receipt=self.cp.store.flush()
        if not receipt.get('verified'):raise PersistenceError('HF_QUESTION_NOT_VERIFIED')
        self.cp.objects.difference_update(getattr(self.cp.store,'discarded_objects',set()))
        for target in self.retirement_roots:
            if target.exists():shutil.rmtree(target)
        self.retirement_roots.clear()
        return receipt
    def run(self,posts):
        self.client.account();self.ingest(posts);done=0;failures=0
        tasks=sorted((t for t in self.state['tasks'].values() if t['status'] not in TERMINAL and (self.live or t['status']!='analyzed') and (not t.get('analysis_retry_at') or utc(t['analysis_retry_at'])<=utc())),key=lambda t:t['deadline_utc'])
        for task in tasks:
            if done>=self.limit or time.monotonic()+180>=self.until:break
            try:self.process(task)
            except (Conflict,PersistenceError):raise
            except Exception as exc:
                code=getattr(exc,'code',None) or (str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{1,99}',str(exc)) else type(exc).__name__);task['last_error']=code
                if getattr(exc,'provider_failure',False):
                    task['attempts']['analysis']=max(0,task['attempts'].get('analysis',0)-1)
                    task['analysis_provider_failures']=task.get('analysis_provider_failures',0)+1
                    task['analysis_retry_at']=(datetime.now(timezone.utc)+timedelta(seconds=exc.retry_after)).isoformat()
                    task['provider_failure_category']=exc.category;task['operator_action_required']=exc.category=='configuration'
                elif code in {'QUESTION_IDENTITY_CHANGED','QUESTION_RULES_OR_SCALE_CHANGED'}:task['status']='blocked_integrity'
                elif code=='QUESTION_CLOSED_OR_PERMISSION':task['status']='closed'
                elif code=='QUESTION_DEADLINE':task['status']='deadline_missed'
                elif task['status']=='capturing' and task['attempts'].get('capture',0)>=3:task['status']='collection_exhausted'
                elif task['status']=='analyzing' and task['attempts'].get('analysis',0)>=3:task['status']='analysis_exhausted'
                self.checkpoint();failures+=1
            if getattr(self.cp.store,'autoflush',False):self.flush_question()
            done+=1
        result=dict(processed=done,failures=failures,tasks=len(self.state['tasks']),live=self.live)
        if getattr(self.cp.store,'batched',False):result.update(checkpoint_policy='one-commit-per-question',hf_successful_commits=getattr(self.cp.store,'successful_commits',0))
        return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['inspect','collect-analyze','live'],default='collect-analyze');parser.add_argument('--limit',type=int,default=1)
    a=parser.parse_args()
    if not 1<=a.limit<=5:raise ValueError('BOUNDED_LIMIT_REQUIRED')
    if a.mode=='live' and os.environ.get('METACULUS_SUBMIT_ENABLED')!='true':raise RuntimeError('LIVE_NOT_ENABLED')
    root=Path(os.environ.get('METACULUS_RUNTIME','runtime')).resolve();root.mkdir(parents=True,exist_ok=True)
    # A trusted workflow owns this directory; never restore onto arbitrary files.
    if any(root.iterdir()):raise RuntimeError('FRESH_RUNTIME_DIRECTORY_REQUIRED')
    configure_credentials();backend=Store(os.environ.get('HF_DATASET','chchen13/forecastagent-data'),os.environ['HF_TOKEN'])
    store=QuestionBatch(backend,root/'hf-staging')
    cp=RuntimeCheckpoint(store,root,key=os.environ.get('METACULUS_CHECKPOINT','metaculus/_control/checkpoint.json'))
    client=platform.Client(os.environ['METACULUS_TOKEN'])
    runner=Runner(cp,client,writer=os.environ.get('METACULUS_WRITER','metaculus-agent-actions-v1'),live=a.mode=='live',limit=a.limit)
    posts,complete=collect_questions(os.environ['METACULUS_TOKEN'])
    if not complete:raise RuntimeError('INCOMPLETE_OPEN_SCAN')
    if a.mode=='inspect':
        client.account()
        return dict(status='restored_checkpoint_inspected',live=False,processed=0,failures=0,tasks=len(runner.state['tasks']),
            legacy_terminal_tasks=sum(t['status']=='legacy_terminal' for t in runner.state['tasks'].values()),
            eligible_open_questions=sum(bool(eligible(post,q)) for post in posts for q in questions(post)),
            budget_restored=(root/'budgets/control/provider-budget.jsonl').is_file(),migration_complete=runner.state.get('migration_complete') is True,submissions=0)
    return runner.run(posts)
if __name__=='__main__':
    # Imported providers may print request excerpts. Only allowlisted counters escape.
    try:
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):result=main()
        print(json.dumps(result))
        if result['failures']:raise SystemExit(1)
    except Exception as exc:
        code=getattr(exc,'code',type(exc).__name__)
        print(json.dumps(dict(status='blocked',code=code if re.fullmatch('[A-Za-z0-9_]{1,80}',str(code)) else 'INTERNAL_ERROR',
                              storage_phase=getattr(exc,'phase',None),http_status=getattr(exc,'http_status',None),retry_after=getattr(exc,'retry_after',None))))
        raise SystemExit(1)
