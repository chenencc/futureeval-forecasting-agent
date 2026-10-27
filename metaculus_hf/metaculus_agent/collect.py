"""Calls shared current acquisition directly, never the historical collector."""
import json,os,subprocess,sys,time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.repository import Repository,atomic_bytes
from pipeline_v2.engine import Engine,collection_profile,find_run
from pipeline_v2.contracts import canonical
from pipeline_v2.acquisition import capture
from pipeline_v2.providers import Budget
from pipeline_v2.platforms import metaculus
from pipeline_v2.intelligence_package import load,export
from pipeline_v2.review_recovery import recover_review
from pipeline_v2.review_lifecycle import all_terminal

class Browser:
    def __init__(self,root):self.root=Path(root);self.process=None
    def __enter__(self):
        from pipeline_v2.browser_pool import BrowserClient
        self.root.mkdir(parents=True,exist_ok=True);endpoint=self.root/'endpoint.json'
        self.process=subprocess.Popen([sys.executable,'-B','-m','pipeline_v2.browser_pool','--root',str(self.root),'--slots','1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        until=time.monotonic()+90
        while time.monotonic()<until:
            if self.process.poll() is not None:raise RuntimeError('BROWSER_EXITED')
            try:
                if BrowserClient(endpoint).health().get('ready_slots')==1:
                    os.environ['FORECAST_BROWSER_POOL']=str(endpoint);return self
            except Exception:pass
            time.sleep(.3)
        self.__exit__(None,None,None);raise RuntimeError('BROWSER_WARMUP_TIMEOUT')
    def __exit__(self,*_):
        os.environ.pop('FORECAST_BROWSER_POOL',None)
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        (self.root/'endpoint.json').unlink(missing_ok=True)

def collect(task,post,root,checkpoint,*,capture_fn=capture,browser=True):
    root=Path(root);data=root/'capture';(data/'control').mkdir(parents=True,exist_ok=True)
    atomic_bytes(data/'control/storage.json',canonical(dict(format='split-question/2')).encode())
    repo=Repository(data);engine=Engine(repo);qid='metaculus:'+task['id']
    if not task.get('capture_run_id'):
        adapted=deepcopy(post)
        if adapted.get('question',{}).get('type')=='discrete':adapted['question']['type']='numeric'
        item=next(x for x in metaculus(adapted) if x['question_id']==qid);engine.question(**item)
        profile=collection_profile();profile['parameters'].update(orderbook=False,submit=False,trade=False)
        profile['parameters'].pop('post_capture_trade_history',None)
        rid=engine.new_run(qid,profile);task.update(capture_run_id=rid,execution_profile=profile)
        aid,owner=engine.start(qid,rid,'admission');engine.finish(qid,rid,'admission',aid,owner,'succeeded');checkpoint()
    rid=task['capture_run_id'];record=repo.get_question(qid);run=find_run(record,rid)
    # Only this runner owns restored files. Interrupted attempts retain every budget.
    for stage,attempts in run['stages'].items():
        if attempts and attempts[-1]['status']=='running':
            engine.repo.commit_patch(qid,None,lambda r,s=stage:engine.terminal(find_run(r,rid)['stages'][s][-1],'failed',code='RUNNER_INTERRUPTED'))
    original=Budget.reserve
    def reserve(self,*args,**kwargs):
        result=original(self,*args,**kwargs);checkpoint();return result
    from contextlib import nullcontext
    with patch.object(Budget,'reserve',reserve), (Browser(root/'browser') if browser else nullcontext()):
        sid=run['snapshot_id'] or capture_fn(engine,qid,rid)
        checkpoint()
        if not all_terminal(repo,repo.get_runtime(qid)):
            for _ in range(2):
                recover_review(repo,qid,sid);checkpoint()
                if all_terminal(repo,repo.get_runtime(qid)):break
        if not all_terminal(repo,repo.get_runtime(qid)):raise RuntimeError('FINAL_REVIEW_PENDING')
        record=repo.get_question(qid);path,_=repo.paths(qid)
        from pipeline_v2.split_repository import index_read
        body=path.parent/index_read(path)['intelligence']['path'];raw,_=export(repo,record,body)
        output=root/'sealed.parquet';atomic_bytes(output,raw);package=load(output)
        task['snapshot_id']=sid;checkpoint();return package
