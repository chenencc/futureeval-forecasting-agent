"""Durable sessions and version-bound replay for pure local tools."""
import json
from ForecastAgent.runtime.progress import fingerprint
from ForecastAgent.runtime.telemetry import MAX_MODEL_ATTEMPTS
from ForecastAgent.readers.saved import version_digest

LOCAL = {'list_channels', 'list_sources', 'list_documents', 'read_document', 'read_dataset_rows',
         'search_saved_text', 'find_passages', 'list_dated_datasets', 'list_official_datasets', 'read_market_snapshot'}


def cache_key(task, name, args):
    if name not in LOCAL:
        return None
    return fingerprint([name, args, task.bundle['request_hash'],
                        task.bundle.get('historical_body_policy'), task.bundle.get('plan'),
                        [(u, version_digest(p), p.get('sha256'), p.get('temporal_status'),
                          p.get('archive_timestamp'), p.get('retrieved_at_utc'), fingerprint(p.get('rows')))
                         for u,p in task.bundle['pages'].items()], task.bundle.get('market_snapshots'),
                        task.catalog(), task.bundle.get('selected_sources'), task.bundle.get('excerpts'),
                        task.budget() if name.startswith('list_') else None])


def replay_local(task, key):
    path = task.directory/'tool_outputs'/f'{key}.json'
    if key and path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved.get('operation_key') != key or fingerprint(saved.get('result')) != saved.get('result_sha256'):
            raise ValueError('Local tool reply hash mismatch; preserve cache for inspection.')
        return {**saved['result'], 'cached_local_reply':True}
    return None


def save_local(task, key, result):
    if key and not result.get('error') and not result.get('blocked'):
        root = task.directory/'tool_outputs'
        root.mkdir(exist_ok=True)
        path = root/f'{key}.json'
        temporary = path.with_suffix('.tmp')
        original = {k:v for k,v in result.items() if k != 'cached_local_reply'}
        temporary.write_text(json.dumps({'operation_key':key, 'result':original,
            'result_sha256':fingerprint(original)}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)


def begin(task, at):
    sessions = task.bundle.setdefault('sessions', [])
    for session in sessions:
        if session['state'] == 'running':
            session.update(state='interrupted', reason='Owner stopped before session finalization')
    for step in task.bundle.get('step_attempts', []):
        if step.get('status') == 'reserved':
            step.update(status='interrupted', recovered_at=at,
                        recovery='Reply unknown; physical reservations remain consumed.')
    current = {'id':len(sessions)+1, 'state':'running', 'started_at':at,
               'attempts_before':len(task.bundle.get('model_attempts', [])),
               'budgets_before':task.budget(), 'turns':[]}
    sessions.append(current)
    return current


def finalize(task, session, reason, at):
    result = task.bundle['result']
    lifetime_exhausted = len(task.bundle.get('model_attempts', [])) >= MAX_MODEL_ATTEMPTS
    if task.bundle['pipeline'] == 'collection' and (lifetime_exhausted or reason in {'model_dispatch_budget', 'program_dispatch_limit', 'deadline'}):
        state = 'budget_exhausted'
    elif result.get('incomplete'):
        state = 'retryable_failure'
    elif result.get('acquisition_complete'):
        state = 'completed'
    else:
        state = 'completed_with_gaps'
    # Dispatch limits can be resumed using the same lifetime ledger, never reset.
    resumable = state in {'retryable_failure', 'budget_exhausted'} and not lifetime_exhausted
    session.update(state=state, reason=reason, finished_at=at,
                   attempts_after=len(task.bundle.get('model_attempts', [])), budgets_after=task.budget(),
                   resumable=resumable)
    result.update(session_state=state, termination_reason=reason, resumable=resumable)
    if state == 'budget_exhausted':
        result['incomplete'] = resumable
    task.bundle['session_state'] = state
