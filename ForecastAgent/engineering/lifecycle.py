"""One stage vocabulary and conservative retry advice; native ledgers own actions."""
import json
from .common import digest, encoded, instant
from .contracts import validate, ContractError

TERMINAL = {'completed', 'completed_with_gaps', 'accepted', 'blocked'}
TRANSITIONS = {
    'queued': {'running', 'blocked'},
    'running': {'completed', 'completed_with_gaps', 'accepted', 'retry_wait', 'submission_unknown', 'blocked'},
    'retry_wait': {'running', 'blocked'},
    'submission_unknown': {'accepted', 'blocked', 'submission_unknown'},
    'completed': set(), 'completed_with_gaps': set(), 'accepted': set(), 'blocked': set()
}


def failure_code(error):
    """Prefer explicit typed/status evidence; do not guess account quota from502."""
    status = getattr(error, 'code', None) or getattr(error, 'status_code', None)
    if isinstance(error, ContractError):
        return 'schema_invalid'
    if status in (401, 403):
        return 'credentials'
    if status in (400, 404, 422):
        return 'invalid_request'
    if status in (429, 402):
        return 'provider_quota'
    if status in (500, 502, 503, 504):
        return 'provider_service'
    if isinstance(error, TimeoutError):
        return 'deadline'
    if isinstance(error, FileNotFoundError):
        return 'source_unavailable'
    return 'unknown_failure'


def latest(db, domain, task, stage):
    row = db.execute('SELECT data FROM lifecycle WHERE domain=? AND task_key=? AND stage=? '
                     'ORDER BY occurred_at DESC,rowid DESC LIMIT 1', (domain, task, stage)).fetchone()
    return json.loads(row[0]) if row else None


def record(db, event):
    validate('lifecycle', event)
    ident = digest(event)
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute('SELECT 1 FROM lifecycle WHERE id=?', (ident,)).fetchone():
            db.rollback()
            return False
        old = latest(db, event['domain'], event['task_key'], event['stage'])
        if event['state'] == 'accepted' and event['stage'] != 'delivery':
            raise ValueError('Acceptance belongs only to delivery')
        if event['state'] == 'retry_wait' and not event['retry_at'] and not event['native']:
            raise ValueError('Retry waiting needs a deadline')
        if event['retry_at'] and instant(event['retry_at']) < instant(event['occurred_at']) and not event['native']:
            raise ValueError('Retry deadline precedes its event')
        if not event['native']:
            if old and instant(event['occurred_at']) < instant(old['occurred_at']):
                raise ValueError('Lifecycle clock moved backwards')
            if old and event['state'] not in TRANSITIONS[old['state']]:
                raise ValueError('Illegal stage transition')
            if old and old['state'] == 'retry_wait' and event['state'] == 'running' and instant(event['occurred_at']) < instant(old['retry_at']):
                raise ValueError('Retry deadline not reached')
            if not old and event['state'] != 'queued':
                raise ValueError('Managed stages begin queued')
        # Native imports are observations, not permission to execute a transition.
        db.execute('INSERT INTO lifecycle VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (ident, event['domain'], event['task_key'], event['stage'], event['state'],
                    event['occurred_at'], event['retry_at'], event['failure_code'], event['source_sha256'], encoded(event).decode()))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return True


def recovery(event, clock):
    """Return advice only; never reopen native tasks, reset budgets or submit."""
    validate('lifecycle', event)
    state, failure = event['state'], event['failure_code']
    if state == 'submission_unknown' or failure == 'unknown_http_outcome':
        action = 'readback_only'
    elif state == 'retry_wait' and not event['retry_at']:
        action = 'requires_repair'
    elif state in TERMINAL:
        action = 'terminal_preserve'
    elif failure == 'budget_exhausted':
        action = 'requires_repair' if not event['retry_at'] else 'wait_deadline' if instant(clock) < instant(event['retry_at']) else 'native_resume_from_checkpoint'
    elif failure in {'credentials', 'invalid_request', 'integrity', 'schema_invalid'}:
        action = 'requires_repair'
    elif state == 'running':
        action = 'inspect_owner_and_checkpoint'
    elif state == 'retry_wait' and instant(clock) < instant(event['retry_at']):
        action = 'wait_deadline'
    else:
        action = 'native_resume_from_checkpoint'
    return {'action': action, 'automatic_execution': False, 'budget_reset': False,
            'native_ledger_authoritative': True}
