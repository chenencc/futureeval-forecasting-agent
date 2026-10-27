"""Bounded final-review recovery over sealed local bodies; never recapture or rewrite them."""
from copy import copy,deepcopy
import hashlib
import json
import os
import re
import time

from .acquisition import review_system,review_schema,review_pending_materials
from .admission import ACCEPTED,assess,admitted_view
from .contracts import PipelineError,canonical,digest,now
from .engine import find_run
from .providers import Budget,Transport,Router
from .repository import atomic_bytes,ensure,file_lock
from .routing import ALIASES,credential_names

POLICY='sealed-body-review-recovery-v1'
MAX_ATTEMPTS=2
TRANSIENT={'HTTP_502','HTTP_503','HTTP_504','TRANSPORT_ERROR'}


class _NoCloudRestore:
    @property
    def repo_id(self):raise PipelineError('REVIEW_LOCAL_BODY_REQUIRED')


def _input(repo,qid,sid):
    # Refuse cloud hydration: this operation has no evidence download capability.
    path,_=repo.paths(qid)
    if path.name=='state.json':
        from .split_repository import index_read
        index=index_read(path);body=path.parent/index['intelligence']['path']
    else:body=path
    ensure(body.is_file(),'REVIEW_LOCAL_BODY_REQUIRED')
    hashed=hashlib.sha256(body.read_bytes()).hexdigest()
    local=copy(repo);local.transport=_NoCloudRestore()
    record=local.get_question(qid);snap=local.read_snapshot(qid,sid)
    ensure(hashlib.sha256(body.read_bytes()).hexdigest()==hashed,'REVIEW_INPUT_CHANGED')
    for m in snap['materials']:
        if m.get('text') is not None:
            ensure(hashlib.sha256(m['text'].encode()).hexdigest()==m['source']['text_sha256'],'REVIEW_BODY_HASH_MISMATCH')
    return record,snap,dict(path=body.relative_to(repo.root).as_posix(),sha256=hashed)


def _path(repo,qid,sid):
    ident=digest(dict(question_id=qid,source_snapshot_id=sid,policy=POLICY))
    return repo.root/'control/review-recovery'/f'{ident}.json',repo.root/'.locks'/f'review-{ident}.lock'


def _save(path,state):
    state['state_hash']=digest({k:v for k,v in state.items() if k!='state_hash'})
    atomic_bytes(path,canonical(state).encode())


def _read(path):
    state=json.loads(path.read_text(encoding='utf-8'))
    ensure(state['state_hash']==digest({k:v for k,v in state.items() if k!='state_hash'}),'REVIEW_RECOVERY_STATE_HASH')
    return state


class ReviewTransport(Transport):
    def request(self,kind,method,url,**kwargs):
        ensure((kind,method,url)==('model','POST','https://openrouter.ai/api/v1/chat/completions'),'REVIEW_ACQUISITION_FORBIDDEN')
        return super().request(kind,method,url,**kwargs)


def recover_review(repo,qid,sid,*,reviewer=None,timeout=180,continuation_id=None):
    """One logical attempt, <=2 physical HTTPs; <=2 logical attempts across restarts.

    A successful repeated call reads the existing receipt. Failed/abandoned attempts
    consume their slot. Budget reservations use a stable identity, never a new run.
    The optional reviewer is for frozen offline validation only.
    """
    ensure(type(timeout) in (int,float) and 0<timeout<=180,'REVIEW_RECOVERY_TIMEOUT')
    path,lock=_path(repo,qid,sid)
    with file_lock(lock,timeout=.1):
        record,snap,body=_input(repo,qid,sid)
        source_run=find_run(record,snap['acquired_by_run_id'])
        ensure(not any(a['status'] in {'running','queued','retry_wait','unknown'} for aa in source_run['stages'].values() for a in aa),'REVIEW_SOURCE_ACTIVE')
        targets=[m['source']['source_id'] for m in snap['materials'] if m['binding']['inclusion']=='pending' and m.get('text')]
        ensure(targets,'REVIEW_RECOVERY_NO_PENDING_BODIES')
        ensure(any(g['code'] in TRANSIENT and str(g.get('detail','')).startswith('Final body-only review failed') for g in snap['gaps']),'REVIEW_RECOVERY_NO_SERVICE_FAILURE')
        identity=dict(policy=POLICY,question_id=qid,source_snapshot_id=sid,evidence_cutoff_at=snap['evidence_cutoff_at'],input_body=body,target_ids=targets)
        state=_read(path) if path.exists() else dict(**identity,status='pending',attempts=[],bindings={})
        ensure(all(state.get(k)==v for k,v in identity.items()),'REVIEW_RECOVERY_INPUT_CHANGED')
        if state['status']=='completed':return state
        manual=False
        if continuation_id is not None:
            ensure(isinstance(continuation_id,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',continuation_id),'INVALID_REVIEW_CONTINUATION')
            grants=state.setdefault('continuations',{})
            if continuation_id not in grants:
                prior=state.get('attempt_limit',MAX_ATTEMPTS)
                ensure(prior==MAX_ATTEMPTS and not grants,'REVIEW_CONTINUATION_LIMIT')
                grants[continuation_id]=dict(granted_at=now(),previous_limit=prior,new_limit=4,prior_attempts=len(state['attempts']))
                state['attempt_limit']=4;_save(path,state)
            manual=len(state['attempts'])<grants[continuation_id]['new_limit']
        if state['status']=='running':
            state['attempts'][-1].update(status='interrupted',finished_at=now(),error_code='REVIEW_RECOVERY_INTERRUPTED',retryable=True)
            state['status']='failed';_save(path,state)
        # Compatibility with the first receipt version: a failed source-specific
        # citation can use the remaining attempt, without erasing that old failure.
        citation_repair=bool(state['attempts'] and state['attempts'][-1].get('error_code')=='REVIEW_RECOVERY_INCOMPLETE' and any(g['code']=='REVIEW_CITATION_UNBOUND' for g in state['attempts'][-1].get('gaps',[])))
        if state['attempts'] and not state['attempts'][-1].get('retryable',False) and not citation_repair and not manual:return state
        ensure(len(state['attempts'])<state.get('attempt_limit',MAX_ATTEMPTS),'REVIEW_RECOVERY_ATTEMPT_LIMIT')
        attempt=dict(number=len(state['attempts'])+1,status='running',started_at=now(),finished_at=None,first_model_http_at=None,
                     usage=None,http_receipts=[],error_code=None,retryable=False)
        state['attempts'].append(attempt);state['status']='running';_save(path,state)
        transport=None
        def progress(usage):
            attempt['usage']=usage;_save(path,state)
        def wire():
            if attempt['first_model_http_at'] is None:attempt['first_model_http_at']=now();_save(path,state)
        def receipt(value):
            attempt['http_receipts'].append(value);_save(path,state)
        try:
            if reviewer is None:
                names=credential_names(source_run,'openrouter')
                for name in names:
                    if not os.environ.get(name):
                        for alias in ALIASES.get(name,()):
                            if os.environ.get(alias):os.environ[name]=os.environ[alias];break
                # Does not require or inspect Tavily/Exa credentials.
                transport=ReviewTransport(Budget(repo.root,per_run=MAX_ATTEMPTS*2),f'review-recovery/{qid}/{sid}',on_model=wire,deadline=time.monotonic()+timeout)
                transport.on_progress=progress
                router=Router(transport,key_names=names,on_receipt=receipt)
                reviewer=lambda value:router.chat(review_system(value),value,max_tokens=6000,json_mode=True,json_schema=review_schema(value),transient_attempts=2)
            bindings=[deepcopy(state['bindings'].get(m['source']['source_id'],m['binding'])) for m in snap['materials']];gaps=[];diagnostics=[]
            review_pending_materials(record,bindings,snap['definition'],reviewer,gaps,cutoff=snap['evidence_cutoff_at'],citation_reference='index',diagnostics=diagnostics)
            ensure(_input(repo,qid,sid)[2]==body,'REVIEW_INPUT_CHANGED')
            selected={b['source_id']:b for b in bindings if b['source_id'] in targets}
            incomplete=any(b['inclusion']=='pending' for b in selected.values())
            error=next((g['code'] for g in gaps if g['code'] in TRANSIENT),None)
            # Final per-source outputs only: retain pending reasons as well as approvals.
            # A returned HTTP response is distinct from a completed binding review.
            attempt.update(binding_results=deepcopy(selected),pending_source_ids=[s for s,b in selected.items() if b['inclusion']=='pending'],review_service_status='failed' if error else 'responded',review_diagnostics=diagnostics,citation_reference='index')
            for m in snap['materials']:
                b=selected.get(m['source']['source_id'])
                if b is not None and b['inclusion']!='pending':
                    ensure(assess(m['source'],m['text'],b,cutoff=snap['evidence_cutoff_at'],stored=True,require_kind=True)==b,'REVIEW_RECOVERY_INVALID_BINDING')
                    state['bindings'][b['source_id']]=b
            if incomplete:
                attempt.update(error_code=error or 'REVIEW_RECOVERY_INCOMPLETE',retryable=error in TRANSIENT or any(g['code']=='REVIEW_CITATION_UNBOUND' for g in gaps))
                state['status']='failed'
            else:
                state.update(status='completed',bindings=selected,completed_at=now(),review_id=digest(dict(source_snapshot_id=sid,bindings=selected,cutoff=snap['evidence_cutoff_at'])))
            attempt.update(status='succeeded' if state['status']=='completed' else 'failed',finished_at=now(),gaps=gaps)
        except Exception as exc:
            attempt.update(status='failed',finished_at=now(),error_code=getattr(exc,'code','REVIEW_RECOVERY_INTERNAL_ERROR'),retryable=getattr(exc,'code',None) in TRANSIENT)
            state['status']='failed'
            if not isinstance(exc,PipelineError):
                _save(path,state);raise
        finally:
            if transport is not None:attempt['usage']=transport.actual_usage()
        _save(path,state)
        return state


def read_reviewed_snapshot(repo,qid,sid):
    """Explicit derived input reader. Original snapshot ID is never reused for new bindings."""
    path,_=_path(repo,qid,sid);state=_read(path)
    ensure(state['status']=='completed','REVIEW_RECOVERY_NOT_COMPLETED')
    _,snap,body=_input(repo,qid,sid)
    ensure(state['input_body']==body,'REVIEW_RECOVERY_INPUT_CHANGED')
    return compose_reviewed_snapshot(snap,state,qid,sid)


def compose_reviewed_snapshot(snap,state,qid,sid):
    """Pure offline reader also used after cloud-only restoration."""
    snap=deepcopy(snap)
    ensure(state['status']=='completed','REVIEW_RECOVERY_NOT_COMPLETED')
    ensure(state['policy']==POLICY and state['question_id']==qid and state['source_snapshot_id']==sid and state['evidence_cutoff_at']==snap['evidence_cutoff_at'],'REVIEW_RECOVERY_INPUT_CHANGED')
    ensure(state['review_id']==digest(dict(source_snapshot_id=sid,bindings=state['bindings'],cutoff=snap['evidence_cutoff_at'])),'REVIEW_RECOVERY_BINDING_HASH')
    expected={m['source']['source_id'] for m in snap['materials'] if m['binding']['inclusion']=='pending' and m.get('text')}
    ensure(set(state['bindings'])==expected,'REVIEW_RECOVERY_SOURCE_SET')
    for m in snap['materials']:
        source_id=m['source']['source_id']
        if source_id in state['bindings']:
            b=state['bindings'][source_id]
            ensure(b['source_id']==source_id and b['inclusion']!='pending' and assess(m['source'],m['text'],b,cutoff=snap['evidence_cutoff_at'],stored=True,require_kind=True)==b,'REVIEW_RECOVERY_INVALID_BINDING')
            m['binding']=deepcopy(b)
    prior=deepcopy(snap['gaps']);has_evidence=bool(admitted_view(snap)['materials'])
    snap['gaps']=[g for g in prior if not (
        (g['code'] in TRANSIENT and str(g.get('detail','')).startswith('Final body-only review failed')) or
        (g['code']=='REVIEW_REQUIRED' and g.get('source_id') in expected) or
        (has_evidence and g['code'] in {'NO_ADMITTED_EVIDENCE','UNVERIFIED_FORECAST_INPUT'}))]
    snap.pop('snapshot_id')
    snap.update(source_snapshot_id=sid,review_recovery=dict(policy=POLICY,review_id=state['review_id'],reviewed_at=state['completed_at'],prior_gaps=prior))
    snap['reviewed_input_id']=digest(snap)
    return snap
