"""Final review handoff persisted with sealing; no evidence or forecast generation."""
from contextlib import contextmanager
from .contracts import digest
from .repository import file_lock,ensure

SERVICE_FAILURES={'HTTP_502','HTTP_503','HTTP_504','TRANSPORT_ERROR'}


def capture_review(materials,gaps):
    pending=[m['source_id'] for m in materials if m['inclusion']=='pending']
    failed=any(g['code'] in SERVICE_FAILURES and str(g.get('detail','')).startswith('Final body-only review failed') for g in gaps)
    return dict(policy='final-review-handoff-v1',citation_reference='index',
        status='needs_recovery' if pending and failed else 'unconfirmed' if pending else 'completed',
        recovery_required=bool(pending and failed),pending_source_ids=pending)


def requirements(record):
    return [r for r in record['runs'] if r['snapshot_id'] and any(
        a.get('final_review',{}).get('recovery_required')
        for a in r['stages'].get('acquisition',[]))]


def outcome(repo,qid,sid):
    from .review_recovery import _path,_read,MAX_ATTEMPTS
    path,_=_path(repo,qid,sid)
    if not path.exists():return dict(status='pending',terminal=False)
    state=_read(path)
    ensure(state['question_id']==qid and state['source_snapshot_id']==sid,'REVIEW_JOB_IDENTITY')
    attempts=state['attempts']
    exhausted=len(attempts)>=state.get('attempt_limit',MAX_ATTEMPTS)
    done=state['status']=='completed' or (state['status']=='failed' and (exhausted or not attempts[-1].get('retryable')))
    # A crashed last attempt still needs reconciliation, but not another HTTP.
    return dict(status=state['status'],terminal=done,attempt_count=len(attempts),state_hash=state['state_hash'],
                error_code=attempts[-1].get('error_code') if attempts else None)


def all_terminal(repo,record):
    return all(outcome(repo,record['question']['id'],r['snapshot_id'])['terminal'] for r in requirements(record))


def guards(repo,record,stack):
    from .review_recovery import _path
    for r in requirements(record):
        stack.enter_context(file_lock(_path(repo,record['question']['id'],r['snapshot_id'])[1],timeout=0))


def export_reviews(repo,record):
    """Final results only; no prompts, attempts, model responses or duplicated bodies."""
    from .review_recovery import _path,_read
    result=[];qid=record['question']['id']
    for r in requirements(record):
        sid=r['snapshot_id'];summary=outcome(repo,qid,sid)
        ensure(summary['terminal'],'REVIEW_NOT_TERMINAL')
        state=_read(_path(repo,qid,sid)[0])
        fields=('policy','question_id','source_snapshot_id','evidence_cutoff_at','input_body','target_ids','status','bindings','review_id','completed_at')
        row={k:state[k] for k in fields if k in state}
        row.update(error_code=summary['error_code'],attempt_count=summary['attempt_count'])
        row['result_hash']=digest(row);result.append(row)
    return result


def export_identity(repo,record):
    return digest(export_reviews(repo,record))
