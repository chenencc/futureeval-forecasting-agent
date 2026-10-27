"""Finite non-submitting acceptance using the real private HF transport."""
import argparse,contextlib,io,json,os,re,shutil,tempfile,uuid
from pathlib import Path
from pipeline_v2.contracts import now,digest
from pipeline_v2.delivery import parquet_payload
from pipeline_v2.intelligence_package import load
from pipeline_v2.routing import configure_credentials
from ForecastAgent.competition.platform import Client
from ForecastAgent.competition.queue import questions
from .platform_adapter import live_request,rule_identity
from .storage import Store,RuntimeCheckpoint,dumps,sha
from .runner import initial_state,blind_post
from .products import context,product,manifest
from .collect import collect
from .analyze import analyze

def storage(store,root):
    identity=uuid.uuid4().hex;key='metaculus/_acceptance/'+identity+'/checkpoint.json'
    state=initial_state('storage-acceptance-'+identity)
    cp=RuntimeCheckpoint(store,root/'writer',key=key)
    store.publish({key:dumps(state)});state=cp.restore()
    value=dict(schema='metaculus-storage-acceptance/1',fixture_only=True,run_id=identity,at=now(),text='Synthetic exact-revision roundtrip only.')
    raw=parquet_payload(value);path=cp.root/'tasks/synthetic/context.parquet';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    ledger=cp.root/'budgets/control/provider-budget.jsonl';ledger.parent.mkdir(parents=True,exist_ok=True);ledger.write_bytes(dumps(dict(reservations=3)))
    receipt=cp.save(state,extra={'metaculus/_acceptance/'+identity+'/context.parquet':raw})
    restored=RuntimeCheckpoint(store,root/'reader',key=key);state2=restored.restore()
    target=restored.root/'tasks/synthetic/context.parquet'
    if load(target)!=value or target.read_bytes()!=raw or json.loads((restored.root/'budgets/control/provider-budget.jsonl').read_text())!={'reservations':3}:raise RuntimeError('STORAGE_ACCEPTANCE_RESTORE')
    target.unlink();cp_path=path.resolve()
    if not cp_path.is_relative_to(cp.root):raise RuntimeError('UNSAFE_ACCEPTANCE_CLEANUP')
    path.unlink();cp.save(state)
    result=dict(status='storage_acceptance_passed',test_only=True,run_id=identity,exact_revision_readback=True,
        parquet_readback=True,restart_restored=True,budget_restored=True,local_payloads_retired=not path.exists() and not target.exists(),revision=receipt['revision'],acquisition_provider_calls=0,submissions=0)
    store.publish({'metaculus/_acceptance/'+identity+'/acceptance.json':dumps(result)})
    return result

def full(store,root,post_id,question_id):
    """Exactly one explicit question; no listener, no submit, no queue adoption."""
    if not str(post_id).isdecimal() or not str(question_id).isdecimal():raise ValueError('NUMERIC_QUESTION_REQUIRED')
    configure_credentials();client=Client(os.environ['METACULUS_TOKEN']);client.account();post=client.post(post_id)
    matches=[q for q in questions(post) if str(q['id'])==str(question_id)]
    if len(matches)!=1:raise RuntimeError('ACCEPTANCE_QUESTION_MISMATCH')
    q=matches[0];clean=blind_post(post,q);identity=uuid.uuid4().hex
    pre='metaculus/_acceptance/'+identity;cp=RuntimeCheckpoint(store,root/'runtime',key=pre+'/checkpoint.json')
    state=initial_state('full-acceptance-'+identity)
    task=dict(id=str(question_id),post_id=str(post_id),post=clean,request=live_request(clean,clean['question']),rule_identity=rule_identity(post,q),
        started_at=now(),run_id=identity,run_purpose='explicit-question-collection-analysis-acceptance',status='capturing',test_only=True)
    state['tasks'][task['id']]=task;store.publish({cp.key:dumps(state)});cp.restore()
    os.environ['FORECAST_V2_SHARED_BUDGET_ROOT']=str(cp.root/'budgets')
    os.environ['FORECAST_V2_ONCE_NAMESPACE']='metaculus-acceptance-'+identity
    os.environ.setdefault('FORECAST_MODEL','nvidia/nemotron-3-super-120b-a12b:free')
    def checkpoint():return cp.save(state)
    taskroot=cp.root/'tasks'/task['id'];taskroot.mkdir(parents=True,exist_ok=True)
    captured=collect(task,clean,taskroot,checkpoint);value=context(task,captured,task['snapshot_id']);raw=product('context',value)
    task['status']='sealed';cp.save(state,extra={pre+'/context.parquet':raw,pre+'/manifest.json':dumps(manifest(task,{'context.parquet':raw}))})
    result=analyze(value,taskroot/'analysis',checkpoint);analysis=product('analysis',result)
    task['status']='analyzed';receipt=cp.save(state,extra={pre+'/analysis.parquet':analysis,pre+'/manifest.json':dumps(manifest(task,{'context.parquet':raw,'analysis.parquet':analysis}))})
    # Durable exact readback happened for both products before native retirement.
    if not taskroot.resolve().is_relative_to(cp.root) or taskroot.is_symlink():raise RuntimeError('UNSAFE_ACCEPTANCE_CLEANUP')
    shutil.rmtree(taskroot);checkpoint()
    report=dict(status='collection_analysis_acceptance_passed',test_only=True,run_id=identity,question_id=str(question_id),snapshot_id=task['snapshot_id'],
        context_sha256=sha(raw),analysis_sha256=sha(analysis),exact_revision_readback=True,revision=receipt['revision'],local_payloads_retired=not taskroot.exists(),submissions=0,
        selected_historical_question=True,new_opening_claimed=False,finished_at=now())
    store.publish({pre+'/acceptance.json':dumps(report)});return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--kind',choices=['storage','full'],default='storage');p.add_argument('--post-id');p.add_argument('--question-id');a=p.parse_args()
    store=Store(os.environ.get('HF_DATASET','chchen13/forecastagent-data'),os.environ['HF_TOKEN'])
    with tempfile.TemporaryDirectory(prefix='metaculus-acceptance-') as tmp:
        return storage(store,Path(tmp)) if a.kind=='storage' else full(store,Path(tmp),a.post_id,a.question_id)
if __name__=='__main__':
    try:
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):result=main()
        print(json.dumps(result))
    except Exception as exc:
        code=getattr(exc,'code',type(exc).__name__);print(json.dumps(dict(status='blocked',code=code if re.fullmatch('[A-Za-z0-9_]{1,80}',str(code)) else 'INTERNAL_ERROR')));raise SystemExit(1)
