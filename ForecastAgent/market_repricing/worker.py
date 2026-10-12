"""Read-only release-pinned acquisition and finite resumable research tasks."""
import contextlib
import hashlib
import os
from pathlib import Path
import sys
import urllib.request

from . import RELEASE_SHA, SUPER
from .common import PublicHTTP, digest, instant, now, policy, read, seal, state, write, verify
from .venues import book, refresh, screen


class ProviderCap(RuntimeError):
    pass


@contextlib.contextmanager
def provider_budget(root, ledger, key, cfg):
    """Physical POST reservations, including failures, persist before transmission."""
    original = urllib.request.urlopen
    replaced = []
    def bounded(request, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        model = url.startswith('https://openrouter.ai/api/')
        search = url.startswith('https://api.tavily.com/search') or url.startswith('https://api.exa.ai/search')
        if model or search:
            records = ledger.setdefault('provider_reservations', [])
            date, run_id = instant(now()).date().isoformat(), os.environ.get('GITHUB_RUN_ID', 'local')
            kind = 'model' if model else 'search'
            same = [r for r in records if r['kind'] == kind]
            dispatch_cap = cfg['maximum_worker_model_attempts_per_dispatch'] if model else cfg['maximum_worker_search_attempts_per_dispatch']
            if sum(r['run_id'] == run_id for r in same) >= dispatch_cap:
                raise ProviderCap('Worker physical HTTP dispatch budget exhausted')
            if model and (sum(r['day'] == date for r in same) >= cfg['maximum_worker_model_attempts_per_utc_day'] or
                          sum(r['key'] == key for r in same) >= cfg['maximum_worker_model_attempts_per_task']):
                raise ProviderCap('Worker cumulative model budget exhausted')
            record = {'key': key, 'kind': kind, 'endpoint': url.split('?')[0], 'day': date,
                      'run_id': run_id, 'reserved_at': now(), 'status': 'reserved'}
            records.append(record); write(Path(root) / 'state.json', ledger)
            try:
                value = original(request, *args, **kwargs)
                record.update(status='response_opened', http_status=value.status)
                return value
            except Exception as exc:
                record.update(status='failed', error_type=type(exc).__name__, http_status=getattr(exc, 'code', None))
                raise
            finally:
                write(Path(root) / 'state.json', ledger)
        return original(request, *args, **kwargs)
    # Frozen release providers may have imported urlopen before this context.
    for module in list(sys.modules.values()):
        if module and getattr(module, '__name__', '').startswith('ForecastAgent') and getattr(module, 'urlopen', None) is original:
            replaced.append(module); module.urlopen = bounded
    urllib.request.urlopen = bounded
    try:
        yield
    finally:
        urllib.request.urlopen = original
        for module in replaced:
            module.urlopen = original
        for module in list(sys.modules.values()):
            if module and getattr(module, '__name__', '').startswith('ForecastAgent') and getattr(module, 'urlopen', None) is bounded:
                module.urlopen = original


def request_for(market):
    # The frozen collector requires numeric IDs. These are explicitly internal
    # compatibility IDs, never Metaculus question IDs or submission identities.
    ident = str(int(digest(market['key'])[:15], 16))
    return {'id': ident, 'question': market['question'], 'question_type': 'binary', 'mode': 'live',
            'resolution_criteria': market['rules'], 'fine_print': '',
            'background': 'Public ' + market['venue'] + ' binary event contract. Platform URL: ' + market['url'] +
                '. Internal acquisition ID only; this is not a Metaculus submission task.',
            'open_time': market.get('created_at'), 'as_of_utc': now(),
            'market_research_key': market['key'], 'synthetic_compatibility_id': True,
            'external_rule_sources': market.get('rules_sources', [])}


def acquire(task, folder):
    from ForecastAgent.releases import v1_0_5 as release
    source = folder / 'analysis-input.json'
    if source.exists():
        if digest(read(source)) != task.get('bundle_sha256'):
            raise ValueError('Frozen original research material changed')
        return read(source)
    request_path = folder / 'collection-request.json'
    request = read(request_path) if request_path.exists() else request_for(task['market'])
    write(request_path, request)
    adapter = release.collect(request, folder / 'retrieval')
    if not adapter.get('release_acquisition'):
        raise RuntimeError('Release acquisition incomplete; native journals retained')
    bundle = release.supplement(adapter, folder, request['id'])
    write(source, bundle)
    task['bundle_sha256'] = digest(bundle)
    task['originals_frozen_at'] = now()
    return bundle


def execute(root, ledger, key, cfg, http, collector=acquire, forecaster=None):
    from datetime import timedelta
    task = ledger['tasks'][key]
    folder = Path(root) / 'tasks' / digest(key)
    folder.mkdir(parents=True, exist_ok=True)
    # Adopt an already sealed result after a process died before updating state.
    result_path = folder / 'analysis' / 'result.json'
    if result_path.exists():
        result = read(result_path)
        task.update(status=result['status'], forecast=result)
        return
    task['executions'] = task.get('executions', 0) + 1
    task['status'] = 'running'
    write(Path(root) / 'state.json', ledger)
    try:
        with provider_budget(root, ledger, key, cfg):
            bundle = collector(task, folder)
            # Save acquisition identity immediately. Replays cannot renew a search.
            write(Path(root) / 'state.json', ledger)
            anchor_path = folder / 'anchor.json'
            if anchor_path.exists():
                anchor = read(anchor_path)
            else:
                current, receipt = refresh(http, task['market'])
                if current['rules_sha256'] != task['market']['rules_sha256']:
                    task.update(status='rules_changed', gap='Exact contract rules changed during collection')
                    return
                if screen(current, now(), cfg)['status'] != 'candidate':
                    raise RuntimeError('Contract no longer eligible at freeze time')
                anchor = book(http, current, cfg)
                if anchor['status'] != 'usable':
                    raise RuntimeError('No usable live two-sided anchor')
                task['market'] = current
                write(anchor_path, anchor)
            if (instant(now()) - instant(anchor['received_at'])).total_seconds() > cfg['maximum_seal_latency_minutes'] * 60:
                task.update(status='expired_anchor', gap='A fresh forecast was not sealed within its preregistered latency')
                return
            if forecaster is None:
                from .forecast import run as forecaster
            result = forecaster(bundle, task['market'], anchor, folder / 'analysis', cfg)
            task.update(status=result['status'], forecast=result)
    except Exception as exc:
        # Local provider caps and corruption need visible intervention; transport
        # retries reuse native reservations and are limited to three executions.
        retry = task['executions'] < cfg['maximum_executions_per_task'] and not isinstance(exc, (ValueError, ProviderCap))
        task.update(status='retry_wait' if retry else 'blocked', error_type=type(exc).__name__,
                    retry_at=(instant(now()) + timedelta(minutes=cfg['minimum_retry_minutes'])).isoformat() if retry else None)
        write(folder / 'failure.json', {'error_type': type(exc).__name__, 'preserved_state': True,
                                      'executions': task['executions'], 'budget_reset': False})
    finally:
        write(Path(root) / 'state.json', ledger)


def run(root, monitor_root, verification_only=False, cfg=None, http=None, collector=acquire, forecaster=None):
    root, monitor_root = Path(root), Path(monitor_root)
    cfg = cfg or policy()
    verify(monitor_root)
    source = read(monitor_root / 'state.json')
    if source['identity']['release_sha'] != RELEASE_SHA or source['identity']['policy_sha256'] != digest(cfg):
        raise ValueError('Queue pin or policy changed')
    if not verification_only and not 0 <= (instant(now()) - instant(source['last_attempt_at'])).total_seconds() <= 3600:
        raise ValueError('Discovery queue is stale')
    ledger = state(root, 'worker', cfg)
    for key, task in source['tasks'].items():
        if key not in ledger['tasks']:
            ledger['tasks'][key] = {'key': key, 'market': task['market'], 'status': 'queued',
                                    'executions': 0, 'source_task_sha256': digest(task)}
        elif ledger['tasks'][key]['source_task_sha256'] != digest(task):
            raise ValueError('Enrolled task identity changed')
    write(root / 'state.json', ledger)
    processed = []
    try:
        if not verification_only:
            # A process timeout leaves a running task; resume its exact saved stage.
            pending = [k for k, t in ledger['tasks'].items() if t['status'] in {'queued', 'retry_wait', 'running'} and
                       (not t.get('retry_at') or instant(t['retry_at']) <= instant(now()))]
            http = http or PublicHTTP(root / 'market-http', 15)
            for key in pending[:cfg['maximum_tasks_per_dispatch']]:
                if ledger['tasks'][key]['executions'] >= cfg['maximum_executions_per_task']:
                    ledger['tasks'][key]['status'] = 'blocked'
                    continue
                # Frozen release collection itself has finite call/tool/deadline caps.
                # On Linux, an outer task alarm also bounds non-provider stalls.
                with task_deadline(cfg['maximum_task_runtime_minutes'] * 60):
                    execute(root, ledger, key, cfg, http, collector, forecaster)
                processed.append(key)
        from collections import Counter
        report = {'verification_only': verification_only, 'processed': processed,
                  'state_distribution': dict(Counter(t['status'] for t in ledger['tasks'].values())),
                  'physical_model_reservations': sum(r['kind'] == 'model' for r in ledger.get('provider_reservations', [])),
                  'physical_search_reservations': sum(r['kind'] == 'search' for r in ledger.get('provider_reservations', [])),
                  'release_sha': RELEASE_SHA, 'model': SUPER, 'submission_enabled': False, 'trading_enabled': False}
        write(root / 'report.json', report)
        return report
    finally:
        write(root / 'state.json', ledger)
        seal(root)


@contextlib.contextmanager
def task_deadline(seconds):
    import signal
    if not hasattr(signal, 'SIGALRM'):
        yield
        return
    old = signal.getsignal(signal.SIGALRM)
    def expired(*unused):
        raise TimeoutError('Research task deadline exhausted')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
