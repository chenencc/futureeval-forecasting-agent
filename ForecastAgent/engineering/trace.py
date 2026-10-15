"""Correlated spans with explicit incomplete attempts and no prompt/body logging."""
from contextlib import contextmanager
import contextvars
import json
import time
import uuid

from .common import now, encoded, safe_metadata
from .contracts import validate
from .lifecycle import failure_code

_parent = contextvars.ContextVar('forecast_engineering_span', default=None)


class Trace:
    def __init__(self, db, domain, task_key, dispatch_id, metadata=None):
        self.db, self.id = db, uuid.uuid4().hex
        metadata = safe_metadata(metadata or {})
        with db:
            db.execute('INSERT INTO traces VALUES(?,?,?,?,?,?)',
                       (self.id, domain, task_key, dispatch_id, now(), encoded(metadata).decode()))

    @contextmanager
    def span(self, stage, metadata=None):
        ident, parent = uuid.uuid4().hex, _parent.get()
        metadata = safe_metadata(metadata or {})
        # Cross-trace nesting is deliberately not inferred as a parent.
        if parent and parent[0] != self.id:
            parent = None
        started = time.monotonic()
        with self.db:
            self.db.execute('INSERT INTO spans VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (ident, self.id, parent[1] if parent else None, stage, 'running', now(), None, None, None, None, encoded(metadata).decode()))
        token = _parent.set((self.id, ident))
        receipt = {'id': ident, 'usage': None, 'outcome': 'completed', 'failure_code': None}
        status, error, usage = 'completed', None, None
        try:
            yield receipt
            validate('usage', receipt['usage'])
            usage = receipt['usage']
            if receipt['outcome'] not in ('completed', 'failed'):
                raise ValueError('Invalid span outcome')
            if receipt['outcome'] == 'failed':
                status, error = 'failed', receipt['failure_code'] or 'unknown_failure'
        except BaseException as exc:
            status, error = 'failed', failure_code(exc)
            raise
        finally:
            _parent.reset(token)
            with self.db:
                self.db.execute('UPDATE spans SET status=?,finished_at=?,duration_ms=?,error_code=?,usage=? WHERE id=?',
                    (status, now(), (time.monotonic() - started) * 1000, error,
                     encoded(usage).decode() if usage is not None else None, ident))


def summary(db):
    rows = list(db.execute('SELECT stage,status,duration_ms,usage FROM spans'))
    stages = {}
    for row in rows:
        group = stages.setdefault(row['stage'], {'completed': 0, 'failed': 0, 'incomplete': 0, 'durations': [], 'known_cost_usd': 0, 'unknown_usage': 0})
        group['incomplete' if row['status'] == 'running' else row['status']] += 1
        if row['duration_ms'] is not None:
            group['durations'].append(row['duration_ms'])
        usage = json.loads(row['usage']) if row['usage'] else None
        if usage is None or usage['cost_usd'] is None:
            group['unknown_usage'] += 1
        else:
            group['known_cost_usd'] += usage['cost_usd']
    for group in stages.values():
        durations = sorted(group.pop('durations'))
        group['p50_ms'] = durations[(len(durations)-1)//2] if durations else None
        group['p95_ms'] = durations[max(0, __import__('math').ceil(.95 * len(durations))-1)] if durations else None
    return {'schema': 'forecast-observability-summary-v1', 'stages': stages,
            'native_state_authoritative': True, 'trace_count': db.execute('SELECT count(*) FROM traces').fetchone()[0]}
