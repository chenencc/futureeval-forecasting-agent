"""Bounded, single-writer orchestration. Live is fail-closed until migration."""
import argparse,contextlib,io,json,os,re,shutil,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.contracts import now,digest
from pipeline_v2.routing import configure_credentials
from ForecastAgent.competition import platform
from ForecastAgent.competition.live import live_request,rule_identity,current
from ForecastAgent.competition.queue import descriptor,questions,utc
from ForecastAgent.monitor_tournament import collect_questions
from .storage import Store,RuntimeCheckpoint,dumps,Conflict,PersistenceError
from .products import context,product,prefix,manifest
from .collect import collect
from .analyze import analyze

TERMINAL={'accepted','already_forecasted','closed','deadline_missed','legacy_terminal','ambiguous_submission'}
def initial_state(writer):return dict(schema='metaculus-checkpoint/1',writer=writer,migration_complete=False,tasks={},runtime={},updated_at=now())
def blind_post(post,question):
    """No community forecasts, outcome labels or other users' comments."""
    parent={k:post.get(k) for k in ('id','title','description','resolution_criteria','fine_print','created_at','status','user_permission')}
    keys=('id','title','description','resolution_criteria','fine_print','type','status','options','scaling','inbound_outcome_count','open_lower_bound','open_upper_bound','unit','open_time','close_time','actual_close_time','scheduled_close_time','spot_scoring_time','scheduled_resolve_time')
    return {**parent,'question':{k:question[k] for k in keys if k in question}}
def eligible(post,q):
    desc=descriptor(post,q)
    return (q.get('status')=='open' and post.get('user_permission') in ('forecaster','curator','admin','creator') and
        q.get('type') in {'binary','multiple_choice','numeric','discrete','date'} and desc['deadline_utc'] and utc(desc['deadline_utc'])>utc())

class Runner:
    def __init__(self,checkpoint,client,*,writer,live=False,limit=1,seconds=3000,collector=collect,analyzer=analyze):
        self.cp,self.client=checkpoint,client;self.root=checkpoint.root;self.writer,self.live=writer,live
        self.limit,self.until=limit,time.monotonic()+seconds;self.collector,self.analyzer=collector,analyzer
        self.state=checkpoint.restore()
        if self.state['writer']!=writer:raise RuntimeError('WRITER_OWNER_MISMATCH')
        if live and self.state.get('migration_complete') is not True:raise RuntimeError('SERVER_HANDOFF_REQUIRED')
        os.environ['FORECAST_V2_SHARED_BUDGET_ROOT']=str(self.root/'budgets')
        os.environ['FORECAST_V2_ONCE_NAMESPACE']='metaculus-public-v1'
        os.environ.setdefault('FORECAST_MODEL','nvidia/nemotron-3-super-120b-a12b:free')
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
            value=context(task,package,task['snapshot_id']);raw=product('context',value)
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
            result=self.analyzer(value,root/'analysis',self.checkpoint)
            if result['snapshot_id']!=task['snapshot_id'] or result['context_sha256']!=task['context_hash']:raise RuntimeError('ANALYSIS_CONTEXT_MISMATCH')
            candidate=result['candidate'];analysis_raw=product('analysis',result)
            task.update(status='analyzed',candidate=candidate,analysis_key=prefix(task)+'/analysis.parquet',analysis_hash=digest(result))
            task['analysis_revision']=self.checkpoint({task['analysis_key']:analysis_raw,prefix(task)+'/manifest.json':dumps(manifest(task,{'context.parquet':raw,'analysis.parquet':analysis_raw}))})['revision']
        if task['status'] in {'analyzed','submitting'} and self.live:
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
        shutil.rmtree(target);task.pop('post',None);task.pop('candidate',None);self.checkpoint()
    def run(self,posts):
        self.client.account();self.ingest(posts);done=0;failures=0
        tasks=sorted((t for t in self.state['tasks'].values() if t['status'] not in TERMINAL),key=lambda t:t['deadline_utc'])
        for task in tasks:
            if done>=self.limit or time.monotonic()+180>=self.until:break
            try:self.process(task)
            except (Conflict,PersistenceError):raise
            except Exception as exc:
                task['last_error']=getattr(exc,'code',type(exc).__name__);self.checkpoint();failures+=1
            done+=1
        return dict(processed=done,failures=failures,tasks=len(self.state['tasks']),live=self.live)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['collect-analyze','live'],default='collect-analyze');parser.add_argument('--limit',type=int,default=1)
    a=parser.parse_args()
    if not 1<=a.limit<=5:raise ValueError('BOUNDED_LIMIT_REQUIRED')
    if a.mode=='live' and os.environ.get('METACULUS_SUBMIT_ENABLED')!='true':raise RuntimeError('LIVE_NOT_ENABLED')
    root=Path(os.environ.get('METACULUS_RUNTIME','runtime')).resolve();root.mkdir(parents=True,exist_ok=True)
    # A trusted workflow owns this directory; never restore onto arbitrary files.
    if any(root.iterdir()):raise RuntimeError('FRESH_RUNTIME_DIRECTORY_REQUIRED')
    configure_credentials();store=Store(os.environ.get('HF_DATASET','chchen13/forecastagent-data'),os.environ['HF_TOKEN'])
    cp=RuntimeCheckpoint(store,root,key=os.environ.get('METACULUS_CHECKPOINT','metaculus/_control/checkpoint.json'))
    client=platform.Client(os.environ['METACULUS_TOKEN'])
    runner=Runner(cp,client,writer=os.environ.get('METACULUS_WRITER','metaculus-agent-actions-v1'),live=a.mode=='live',limit=a.limit)
    posts,complete=collect_questions(os.environ['METACULUS_TOKEN'])
    if not complete:raise RuntimeError('INCOMPLETE_OPEN_SCAN')
    return runner.run(posts)
if __name__=='__main__':
    # Imported providers may print request excerpts. Only allowlisted counters escape.
    try:
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):result=main()
        print(json.dumps(result))
        if result['failures']:raise SystemExit(1)
    except Exception as exc:
        code=getattr(exc,'code',type(exc).__name__)
        print(json.dumps(dict(status='blocked',code=code if re.fullmatch('[A-Za-z0-9_]{1,80}',str(code)) else 'INTERNAL_ERROR')))
        raise SystemExit(1)
