"""Separate non-transmitted budget holds from physical provider reservations."""
import os
from datetime import timedelta
from pathlib import Path

from .common import digest, instant, now, read, write


class ProviderCap(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__('Local provider budget: ' + receipt['scope'])


class CollectionIncomplete(RuntimeError):
    pass


def availability(ledger, key, cfg, kind='model', required=1):
    clock = instant(now())
    date = clock.date().isoformat()
    run_id = os.environ.get('GITHUB_RUN_ID', 'local')
    records = [r for r in ledger.get('provider_reservations', []) if r['kind'] == kind]
    checks = []
    if kind == 'model':
        checks = [('task_lifetime', sum(r['key'] == key for r in records),
                   cfg['maximum_worker_model_attempts_per_task'], None),
                  ('utc_day', sum(r['day'] == date for r in records),
                   cfg['maximum_worker_model_attempts_per_utc_day'],
                   (clock.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat())]
    checks.append(('dispatch', sum(r['run_id'] == run_id for r in records),
                   cfg['maximum_worker_model_attempts_per_dispatch'] if kind == 'model'
                   else cfg['maximum_worker_search_attempts_per_dispatch'], now()))
    for scope, used, limit, retry_at in checks:
        if used + required > limit:
            return {'key': key, 'kind': kind, 'scope': scope, 'used': used, 'limit': limit,
                    'retry_at': retry_at, 'run_id': run_id, 'observed_at': now(),
                    'transmitted': False, 'required': required, 'budget_reset': False}
    return None


def executions_used(task):
    return (task.get('executions', 0) - task.get('budget_wait_executions', 0)
            - task.get('program_wait_executions', 0))


def recover_context_waits(root, ledger):
    """Credit one proven local, non-transmitted context interruption; keep all usage."""
    recovered = []
    for key, task in ledger['tasks'].items():
        if task['status'] not in {'blocked', 'retry_wait'} or task.get('context_hold_recovery'):
            continue
        if task.get('error_type') != 'CollectionIncomplete' or not task.get('last_attempt_at'):
            continue
        folder = Path(root) / 'tasks' / digest(key)
        marker = folder / 'development-intelligence/collection-status.json'
        failure = folder / 'failure.json'
        if not marker.exists() or not failure.exists():
            continue
        report = read(marker).get('collector_report') or {}
        native = report.get('state') or {}
        if (native.get('termination_reason') != 'context_projection_failure'
                or native.get('interrupted') is not True
                or read(failure).get('error_type') != 'CollectionIncomplete'):
            continue
        if any(r['key'] == key and instant(r['reserved_at']) >= instant(task['last_attempt_at'])
               for r in ledger.get('provider_reservations', [])):
            continue
        receipt = {'at': now(), 'reason': 'Verified context interruption before any paid provider transmission',
            'original_status': task['status'], 'original_executions': task.get('executions', 0),
            'original_failure': read(failure), 'native_collection_state': native,
            'provider_reservations_unchanged': True, 'excluded_program_executions': 1, 'budget_reset': False}
        write(folder / 'context-hold-recovery.json', receipt)
        task['context_hold_recovery'] = receipt
        task['program_wait_executions'] = task.get('program_wait_executions', 0) + 1
        if task.get('terminal_result'):
            task.setdefault('historical_terminal_results', []).append(task.pop('terminal_result'))
        task.update(status='retry_wait', retry_at=None)
        recovered.append(key)
    return recovered


def hold(task, receipt, started=False):
    if started:
        task['budget_wait_executions'] = task.get('budget_wait_executions', 0) + 1
    task.update(status='blocked' if receipt['scope'] == 'task_lifetime' else 'budget_wait',
                retry_at=receipt['retry_at'], budget_hold=receipt, error_type='ProviderCap')


def refresh_waits(ledger, cfg):
    """An authorized raised ceiling releases only satisfied local budget holds."""
    for key, task in ledger['tasks'].items():
        receipt = task.get('budget_hold') or {}
        if task['status'] != 'budget_wait' or receipt.get('scope') not in {'utc_day', 'dispatch'}:
            continue
        if availability(ledger, key, cfg, receipt.get('kind', 'model'), receipt.get('required', 1)) is None:
            if task.get('retry_at'):
                task.setdefault('budget_releases', []).append({'at': now(), 'previous_hold': receipt,
                    'reason': 'Recorded budget condition is now satisfied', 'budget_reset': False})
                task['retry_at'] = None


def native_denials(folder):
    """Only final pre-transmission ProviderCap receipts qualify; originals stay intact."""
    import hashlib
    folder = Path(folder).resolve()
    found = []
    for bundle_path in folder.glob('development-intelligence/retrieval/**/collection/bundle.json'):
        bundle = read(bundle_path)
        for attempt in bundle.get('model_attempts', []):
            path = (bundle_path.parent / attempt['path']).resolve()
            if not path.is_relative_to(bundle_path.parent.resolve()) or not path.is_file():
                raise ValueError('Native model receipt path changed')
            if hashlib.sha256(path.read_bytes()).hexdigest() != attempt['sha256']:
                raise ValueError('Native model receipt hash changed')
            record = read(path)
            if (record.get('status') == 'transport_error' and record.get('error') == 'ProviderCap'
                    and record.get('http_status') is None):
                found.append({'path': str(path.relative_to(folder)), 'sha256': attempt['sha256'],
                              'at': record['started_at_utc']})
    return found


def recover_legacy(root, ledger):
    """Repair only proven legacy budget-only retries, without renewing any HTTP cap."""
    recovered = []
    for key, task in ledger['tasks'].items():
        if task['status'] not in {'retry_wait', 'running', 'blocked'} or task.get('legacy_budget_recovery'):
            continue
        folder = Path(root) / 'tasks' / digest(key)
        failure = folder / 'failure.json'
        if not failure.exists() or read(failure).get('error_type') != 'FileNotFoundError':
            continue
        denials = native_denials(folder)
        last = task.get('last_attempt_at')
        if not last or not any(instant(d['at']) >= instant(last) for d in denials):
            continue
        if any(r['key'] == key and instant(r['reserved_at']) >= instant(last)
               for r in ledger.get('provider_reservations', [])):
            continue
        # The last execution made no provider request. Earlier executions are
        # retained as charged unless independently proven by a later audit.
        receipt = {'at': now(), 'native_denials': denials, 'original_failure': read(failure),
                   'excluded_executions': 1, 'original_executions': task['executions'],
                   'provider_reservations_unchanged': True, 'budget_reset': False}
        write(folder / 'legacy-budget-recovery.json', receipt)
        task['legacy_budget_recovery'] = receipt
        task['budget_wait_executions'] = task.get('budget_wait_executions', 0) + 1
        task.update(status='budget_wait', retry_at=None)
        recovered.append(key)
    return recovered


def require_package(directory, result, denials):
    if denials:
        raise ProviderCap(denials[-1])
    package = directory / 'final-map-package.json'
    if not package.exists():
        package = directory / 'retrieval/development-channels/package.json'
    if not package.exists():
        raise CollectionIncomplete('Collection did not produce a package; saved collection-status.json retains the native reason')
    return package
