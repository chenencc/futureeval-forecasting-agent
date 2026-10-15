"""Additive native adapters. Observations and recovery advice never execute tasks."""
from contextlib import contextmanager
from pathlib import Path
import json
import os

from .common import digest, instant, now, read
from .lifecycle import record, recovery
from .snapshots import open_readonly
from .trace import Trace
from .contracts import validate, ContractError


def observation(domain, key, state, stage, source, at, retry=None, failure=None):
    return {'schema': 'forecast-lifecycle-event-v1', 'domain': domain, 'task_key': key,
            'stage': stage, 'state': state, 'occurred_at': at, 'retry_at': retry,
            'failure_code': failure, 'source_sha256': digest(source), 'native': True}


def state_for(status, *, delivery_verified=False):
    if status in ('accepted', 'submitted', 'already_predicted'):
        return ('accepted', 'delivery', None) if delivery_verified else ('submission_unknown', 'delivery', 'unknown_http_outcome')
    if status == 'submission_unknown':
        return 'submission_unknown', 'delivery', 'unknown_http_outcome'
    if status in ('completed', 'analyzed', 'forecast_ready'):
        return 'completed', 'analysis', None
    if status in ('baseline_only', 'closed_with_gaps', 'collected_with_gaps'):
        return 'completed_with_gaps', 'acquisition', None
    if status in ('collected', 'acquired'):
        return 'completed', 'acquisition', None
    if status in ('queued', 'pending', 'discovered'):
        return 'queued', 'acquisition', None
    if status in ('running', 'collecting', 'analyzing', 'submitting'):
        return 'running', 'delivery' if status == 'submitting' else 'analysis' if status == 'analyzing' else 'acquisition', None
    if status in ('retry_wait', 'budget_wait', 'retry_pending'):
        return 'retry_wait', 'acquisition', 'budget_exhausted' if status == 'budget_wait' else None
    return 'blocked', 'acquisition', 'unknown_failure'


def sync(db, domain, database):
    count, unchanged, plans = 0, 0, {}
    with open_readonly(database) as native:
        if domain == 'market':
            rows = native.execute('SELECT market_key AS task_key,observed_at,data FROM latest_tasks')
        elif domain == 'competition':
            rows = native.execute('SELECT question_key AS task_key,observed_at,data FROM latest_tasks')
        else:
            raise ValueError('Unknown native domain')
        for row in rows:
            task = json.loads(row['data'])
            status = task.get('status') or task.get('stage') or 'unknown'
            verified = False
            if domain == 'competition':
                receipt = native.execute('SELECT r.status,r.verified_binding,r.confirmed_at,r.payload,r.data,f.payload AS candidate_payload '
                    'FROM latest_receipts r LEFT JOIN forecasts f ON f.id=r.forecast_id WHERE r.question_key=?', (row['task_key'],)).fetchone()
                verified = bool(receipt and receipt['status'] == 'accepted' and receipt['verified_binding'])
                if verified:
                    payload = json.loads(receipt['payload'])
                    candidate = json.loads(receipt['candidate_payload']) if receipt['candidate_payload'] else None
                    try:
                        validate('receipt', {'schema': 'forecast-delivery-receipt-v1', 'question_key': row['task_key'],
                            'candidate_payload_sha256': digest(candidate), 'status': 'accepted', 'confirmed_at': receipt['confirmed_at'],
                            'payload': payload, 'evidence_record_sha256': digest(json.loads(receipt['data']))}, candidate=candidate)
                    except ContractError:
                        verified = False
                if verified:
                    status = 'accepted'
            state, stage, code = state_for(status, delivery_verified=verified)
            evt = observation(domain, row['task_key'], state, stage, task, row['observed_at'], task.get('retry_at') or task.get('retry_after_utc'), code)
            if record(db, evt):
                count += 1
            else:
                unchanged += 1
            plans[row['task_key']] = {'state': state, 'stage': stage, 'native_status': status,
                                     **recovery(evt, now())}
    return {'domain': domain, 'new_observations': count, 'unchanged_observations': unchanged,
            'tasks': plans, 'native_writes': 0, 'provider_calls': 0,
            'historical_trace_durations_inferred': False}


def observed_call(db, domain, task_key, dispatch_id, stage, callback):
    trace = Trace(db, domain, task_key, dispatch_id)
    with trace.span(stage):
        return callback()


@contextmanager
def market_worker(db, dispatch_id):
    """Trace actual invoked functions. Frozen worker, prompts and budgets stay intact."""
    import inspect
    from ForecastAgent.market_repricing import worker, forecast
    original_execute, original_forecast = worker.execute, forecast.run

    def execute(*args, **kwargs):
        bound = inspect.signature(original_execute).bind(*args, **kwargs)
        bound.apply_defaults()
        task = bound.arguments['ledger']['tasks'][bound.arguments['key']]
        trace = Trace(db, 'market', bound.arguments['key'], dispatch_id,
                      {'native_executions_before': task.get('executions', 0)})
        collector = bound.arguments['collector']
        def collect(*a, **k):
            with trace.span('acquisition_and_mapping'):
                return collector(*a, **k)
        bound.arguments['collector'] = collect
        with trace.span('task_execution') as span:
            result = original_execute(*bound.args, **bound.kwargs)
            if task.get('status') in ('retry_wait', 'budget_wait', 'blocked'):
                span['outcome'] = 'failed'
                span['failure_code'] = 'budget_exhausted' if task['status'] == 'budget_wait' else 'unknown_failure'
            return result

    def score(*args, **kwargs):
        # A new trace is correlated by the same exact market key and dispatch.
        market = args[1] if len(args) > 1 else kwargs['market']
        trace = Trace(db, 'market', market['key'], dispatch_id)
        with trace.span('analysis'):
            return original_forecast(*args, **kwargs)

    worker.execute, forecast.run = execute, score
    try:
        yield
    finally:
        worker.execute, forecast.run = original_execute, original_forecast
