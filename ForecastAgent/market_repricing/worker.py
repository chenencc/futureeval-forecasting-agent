"""Integrated development acquisition/maps and finite read-only research tasks."""
import contextlib
import hashlib
import os
from pathlib import Path
import sys
import urllib.request

from . import ENGINE_SHA, ENGINE_BRANCH, SUPER
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
        search = any(url.startswith(endpoint) for endpoint in (
            'https://api.tavily.com/search', 'https://api.tavily.com/extract', 'https://api.exa.ai/search'))
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
            if url == 'https://api.tavily.com/search' and isinstance(request, urllib.request.Request):
                import json
                depth = json.loads(request.data).get('search_depth', 'basic')
                record.update(search_depth=depth, credits_estimate=2 if depth == 'advanced' else 1)
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
    from .search_credentials import transport
    routed = transport(bounded, root, ProviderCap)
    # Development providers may have imported urlopen before this context.
    for module in list(sys.modules.values()):
        if module and getattr(module, '__name__', '').startswith('ForecastAgent') and getattr(module, 'urlopen', None) is original:
            replaced.append(module); module.urlopen = routed
    urllib.request.urlopen = routed
    try:
        yield
    finally:
        urllib.request.urlopen = original
        for module in replaced:
            module.urlopen = original
        for module in list(sys.modules.values()):
            if module and getattr(module, '__name__', '').startswith('ForecastAgent') and getattr(module, 'urlopen', None) is routed:
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
    from ForecastAgent.intelligence import pipeline
    from ForecastAgent.intelligence.research_map import enable
    from ForecastAgent.intelligence.worker import remaining_budget
    source = folder / 'analysis-input.json'
    marker = folder / 'material-integrity.json'
    from .evidence_capture import VERSION as capture_version, policy_identity, capture_adapter
    new_capture = task.get('acquisition_policy') == capture_version
    final_capture = folder / 'evidence-capture-v2' / 'analysis-input.json'
    if new_capture and final_capture.exists():
        saved = read(final_capture)
        if digest(saved) != read(final_capture.parent / 'analysis-integrity.json')['bundle_sha256']:
            raise ValueError('Frozen evidence-capture packet changed')
        task.update(bundle_sha256=digest(saved), analysis_stage='analysis-evidence-v2')
        return saved
    recovered = folder / 'body-recovery-v1' / 'analysis-input.json'
    if recovered.exists():
        saved = read(recovered)
        if digest(saved) != read(recovered.parent / 'analysis-integrity.json')['bundle_sha256']:
            raise ValueError('Frozen recovered originals changed')
        task.update(bundle_sha256=digest(saved), analysis_stage='analysis-recovered-v1')
        return saved
    if source.exists():
        expected = task.get('bundle_sha256') or (read(marker).get('bundle_sha256') if marker.exists() else None)
        if digest(read(source)) != expected:
            raise ValueError('Frozen original research material changed')
        task['bundle_sha256'] = expected
        bundle = read(source)
        from .materials import prepare
        view, _ = prepare(bundle)
        if new_capture:
            return repair_materials(task, folder, bundle)
        if len(view['pages']) >= 2:
            return bundle
        return repair_materials(task, folder, bundle)
    request_path = folder / 'collection-request.json'
    request = read(request_path) if request_path.exists() else enable(
        request_for(task['market']), predictive_focus=True, scoring_delivery=True)
    from .search_policy import identity as search_identity, adapter as search_adapter, guide as search_guide
    profile = task.get('market_search_profile')
    if profile is not None:
        if profile != search_identity() or request.get('market_search_profile') not in (None, profile):
            raise ValueError('Frozen market search implementation changed')
        if request_path.exists() and request.get('market_search_profile') is None:
            raise ValueError('Cannot grant mixed search allowance to an existing collection')
        request['market_search_profile'] = profile
    request['collection_model'] = SUPER
    if new_capture:
        expected = policy_identity()
        if request.get('market_capture_identity') not in (None, expected):
            raise ValueError('Frozen capture adapter implementation changed')
        request.update(market_acquisition_policy=capture_version, market_capture_identity=expected,
                       acquisition_focus='raw_recall')
    write(request_path, request)
    directory = folder / 'development-intelligence'
    # Replays use the same request clock and the development worker's cumulative
    # decision/failure reservations, not fresh per-dispatch collection allowances.
    # The registered reader in this development engine is read_sources, not
    # fetch_page. State this explicitly without changing the pinned engine.
    from ForecastAgent.runtime import guidance
    from unittest.mock import patch
    original_guidance = guidance.collection_system
    def guided(*args, **kwargs):
        return original_guidance(*args, **kwargs) + (
            '\nAfter a search returns URLs, call read_sources with exact discovered URLs '
            'to capture external original bodies before further map planning. fetch_page '
            'is unavailable. Market pages supply rules/prices, not independent event evidence. '
            'Use registered tools only; respect each tool schema. Missing reports never prove NO.') + search_guide(args[0])
    with remaining_budget(directory), patch.object(guidance, 'collection_system', guided), \
            capture_adapter() if new_capture else contextlib.nullcontext(), \
            search_adapter() if profile is not None else contextlib.nullcontext():
        result = pipeline.collect(request, directory, clock_utc=request['as_of_utc'],
                                  recover_data=True, research_map=True, channel_tools=True)
    package = directory / 'final-map-package.json'
    if not package.exists():
        package = directory / 'retrieval/development-channels/package.json'
    bundle = read(package)
    if str(bundle['request']['id']) != request['id']:
        raise ValueError('Development material identity mismatch')
    write(folder / 'intelligence-report.json', result['report'])
    write(marker, {'bundle_sha256': digest(bundle), 'engine_sha': ENGINE_SHA,
                   'engine_branch': ENGINE_BRANCH, 'source_package': str(package.relative_to(folder))})
    write(source, bundle)
    task['bundle_sha256'] = digest(bundle)
    task['originals_frozen_at'] = now()
    return repair_materials(task, folder, bundle)


def repair_materials(task, folder, bundle):
    from .materials import prepare, recover
    view, audit = prepare(bundle)
    from .evidence_capture import VERSION as capture_version, packet
    new_capture = task.get('acquisition_policy') == capture_version
    if not new_capture and len(view['pages']) >= 2:
        return bundle
    root = folder / ('evidence-capture-v2' if new_capture else 'body-recovery-v1')
    if new_capture:
        from .evidence_capture import recover, verify_packet
        intake = list(folder.glob('development-intelligence/**/market-intake/first-packet.json'))
        for path in intake:
            verify_packet(path)
        # Preserve the early search-time packet independently of later native supplements.
        write(root / 'intake-link.json', {'initial_packet_paths': [str(p.relative_to(folder)) for p in intake],
            'initial_packet_sha256': [digest(read(p)) for p in intake],
            'early_intake_available': bool(intake), 'raw_parent_sha256': digest(bundle)})
    child, report = recover(bundle, root)
    view, audit = prepare(child)
    reading_input = root / 'reading-input.json'
    if reading_input.exists():
        view = read(reading_input)
    else:
        # Newly captured material belongs to a new explicit information clock.
        # The old native request remains in the immutable parent package.
        import json
        view['request']['as_of_utc'] = now()
        view['request_hash'] = hashlib.sha256(json.dumps(view['request'], sort_keys=True).encode()).hexdigest()
        write(reading_input, view)
    # Review the repaired external originals only; blocked and market pages
    # remain in the raw parent and capture archive, outside analysis input.
    if view['pages']:
        from ForecastAgent.research_loop import post_supplement
        from ForecastAgent.intelligence.worker import attempts, POLICY
        used = attempts(folder / 'development-intelligence')
        allowance = max(0, min(2, POLICY['model_http'] - used['http']))
        try:
            child, review = post_supplement.run(view, root / 'map-review', http_cap=allowance,
                                               failure_cap=allowance, execute=allowance > 0)
            write(root / 'map-review-summary.json', review)
        except Exception as exc:
            # A map is optional for original-only scoring. Never discard bodies
            # because graph formatting or the Super service failed.
            child = view
            write(root / 'map-review-failure.json', {'error_type': type(exc).__name__,
                'preserved_originals': True, 'no_retry_or_budget_reset': True})
    else:
        child = view
    write(root / 'analysis-input.json', child)
    write(root / 'analysis-integrity.json', {'bundle_sha256': digest(child),
        'raw_parent_sha256': digest(bundle), 'body_recovery_report': report})
    if new_capture:
        packet(child, root / 'packets', 'final_pre_forecast_evidence_freeze')
        from .common import instant
        first = read(intake[0]) if intake else None
        captures = [p.get('retrieved_at_utc') for p in child.get('pages', {}).values() if p.get('retrieved_at_utc')]
        task['capture_summary'] = {'policy': capture_version, 'early_intake_available': bool(first),
            'first_packet_frozen_at': first.get('frozen_at') if first else None,
            'final_reading_cutoff_at': view['request']['as_of_utc'],
            'first_external_capture_at': min(captures, key=instant) if captures else None,
            'enrollment_to_first_packet_minutes': (instant(first['frozen_at']) - instant(task['enrolled_at'])).total_seconds()/60
                if first and task.get('enrolled_at') else None,
            'external_body_count': len(view['pages']),
            'external_full_body_count': audit.get('external_full_body_count', len(view['pages'])),
            'rules_designated_gaps': report['checklist']['rules_designated_gaps'],
            'unattempted_sources': len(report['checklist']['unattempted_sources']),
            'semantic_completeness_verified': False}
    task.update(bundle_sha256=digest(child), analysis_stage='analysis-evidence-v2' if new_capture else 'analysis-recovered-v1',
                evidence_status='external_originals' if view['pages'] else 'insufficient_evidence')
    return child


def execute(root, ledger, key, cfg, http, collector=acquire, forecaster=None):
    from datetime import timedelta
    task = ledger['tasks'][key]
    cfg = forecast_policy(task, cfg)
    folder = Path(root) / 'tasks' / digest(key)
    folder.mkdir(parents=True, exist_ok=True)
    # Adopt an already sealed result after a process died before updating state.
    result_path = folder / task.get('analysis_stage', 'analysis') / 'result.json'
    terminal_path = result_path.parent / 'terminal-result.json'
    if terminal_path.exists():
        result_path = terminal_path
    if result_path.exists():
        result = read(result_path)
        task.update(status=result['status'], forecast=result)
        return
    task['executions'] = task.get('executions', 0) + 1
    task['status'] = 'running'
    task['last_attempt_at'] = now()
    write(Path(root) / 'state.json', ledger)
    try:
        with provider_budget(root, ledger, key, cfg):
            bundle = collector(task, folder)
            # Save acquisition identity immediately. Replays cannot renew a search.
            write(Path(root) / 'state.json', ledger)
            stage = folder / task.get('analysis_stage', 'analysis')
            anchor_path = (stage / 'anchor.json') if task.get('analysis_stage') else folder / 'anchor.json'
            if anchor_path.exists():
                anchor = read(anchor_path)
            else:
                current, receipt = refresh(http, task['market'])
                if current['rules_sha256'] != task['market']['rules_sha256']:
                    task.update(status='rules_changed', gap='Exact contract rules changed during collection')
                    return
                if screen(current, now(), cfg, check_newness=False)['status'] != 'candidate':
                    raise RuntimeError('Contract no longer eligible at freeze time')
                anchor = book(http, current, cfg)
                # A shallow valid two-sided book supplies observational context
                # only, never depth-qualified metrics or trade eligibility.
                if anchor['status'] == 'shallow_book':
                    bid, ask = anchor.get('yes_bid'), anchor.get('yes_ask')
                    if (isinstance(bid, (int,float)) and isinstance(ask, (int,float)) and
                        0 < bid <= ask < 1 and anchor.get('yes_bid_size', 0) > 0 and anchor.get('yes_ask_size', 0) > 0):
                        anchor.update(status='usable', observed_book_status='shallow_book',
                            midpoint=(bid+ask)/2, eligible_for_model_metrics=False,
                            observational_context_only=True,
                            quality_flags=list(anchor.get('quality_flags', []))+['shallow_book'])
                if anchor['status'] != 'usable':
                    write(stage / 'unusable-anchor.json', anchor)
                    raise RuntimeError('No usable live two-sided anchor')
                task['market'] = current
                write(anchor_path, anchor)
            if (instant(now()) - instant(anchor['received_at'])).total_seconds() > cfg['maximum_seal_latency_minutes'] * 60:
                task.update(status='expired_anchor', gap='A fresh forecast was not sealed within its preregistered latency')
                return
            from .materials import prepare
            view, body_audit = prepare(bundle)
            if forecaster is None:
                from .forecast import run as forecaster
            if not view['pages']:
                result = baseline_result(anchor, cfg, 'insufficient_external_originals', body_audit)
                write(stage / 'result.json', result)
            else:
                result = forecaster(bundle, task['market'], anchor, stage, cfg)
                if anchor.get('eligible_for_model_metrics') is False:
                    result.update(eligible_for_model_metrics=False, observational_context_only=True)
                    write(stage / 'result.json', result)
                if result['status'] != 'completed':
                    write(stage / 'attempted-model-result.json', result)
                    result = baseline_result(anchor, cfg, 'model_did_not_return_valid_score', body_audit)
                    write(stage / 'terminal-result.json', result)
            task.update(status=result['status'], forecast=result)
    except Exception as exc:
        # Local provider caps and corruption need visible intervention; transport
        # retries reuse native reservations and are limited to three executions.
        retry = task['executions'] < cfg['maximum_executions_per_task'] and not isinstance(exc, (ValueError, ProviderCap))
        task.update(status='retry_wait' if retry else 'blocked', error_type=type(exc).__name__,
                    retry_at=(instant(now()) + timedelta(minutes=cfg['minimum_retry_minutes'])).isoformat() if retry else None)
        if not retry:
            terminal = {'status': 'blocked', 'reason': type(exc).__name__, 'sealed_at': now(),
                'eventual_probability_yes': None, 'price_means': None, 'arms': {},
                'eligible_for_model_metrics': False, 'preserved_state': True, 'budget_reset': False}
            write(folder / 'unpriced-terminal-result.json', terminal)
            task['terminal_result'] = terminal
        write(folder / 'failure.json', {'error_type': type(exc).__name__,
                                      'error_message': str(exc)[:500], 'preserved_state': True,
                                      'executions': task['executions'], 'budget_reset': False})
    finally:
        write(Path(root) / 'state.json', ledger)


def forecast_policy(task, cfg):
    # Old enrolled tasks and pending retries keep their frozen forecast protocol.
    if task.get('analysis_arms'):
        cfg = cfg | {'analysis_arms': list(task['analysis_arms'])}
    if task.get('price_schedule'):
        return cfg | {'price_schedule': task['price_schedule']}
    return cfg | {'horizons_hours': [72, 168], 'price_target_origin': 'forecast_anchor_at'}


def baseline_result(anchor, cfg, reason, audit):
    """A visible market-only control is never a model forecast or event score."""
    from .tracking import schedule
    price_schedule = cfg.get('price_schedule') or schedule(anchor['received_at'], cfg['horizons_hours'], 'forecast_anchor_at')
    return {'price_schedule': price_schedule, 'status': 'baseline_only', 'reason': reason, 'anchor_at': anchor['received_at'],
        'anchor': anchor, 'sealed_at': now(), 'timely': False,
        'baseline': {str(h): anchor['midpoint'] for h in cfg['horizons_hours']},
        'arms': {}, 'eventual_probability_yes': None, 'score_source': 'market_no_change_control',
        'eligible_for_model_metrics': False, 'body_audit': audit,
        'no_orders_or_platform_forecasts': True, 'development_only': True}


def pending_keys(ledger):
    due = [k for k, t in ledger['tasks'].items() if t['status'] in {'queued', 'retry_wait', 'running'} and
           (not t.get('retry_at') or instant(t['retry_at']) <= instant(now()))]
    # Legacy checkpoints have execution counts but no last-attempt field.
    # Interleave two untouched tasks and one oldest retry; neither class starves.
    order = lambda k: (ledger['tasks'][k].get('last_attempt_at', ''),
                        ledger['tasks'][k].get('enrolled_at') or '', k)
    fresh = sorted((k for k in due if not ledger['tasks'][k].get('executions', 0)), key=order)
    retries = sorted((k for k in due if ledger['tasks'][k].get('executions', 0)), key=order)
    if not fresh or not retries:
        return fresh + retries
    result = []
    while fresh or retries:
        result.extend(fresh[:2]); del fresh[:2]
        result.extend(retries[:1]); del retries[:1]
    return result


def quarantine_invalid_results(root, ledger, reopen=False):
    from .materials import prepare
    for key, task in ledger['tasks'].items():
        if task['status'] != 'completed' or task.get('analysis_stage'): continue
        folder = root / 'tasks' / digest(key)
        source = folder / 'analysis-input.json'
        if not source.exists(): continue
        view, audit = prepare(read(source))
        if view['pages']: continue
        audit_path = folder / 'invalid-result-quarantine.json'
        if reopen or not audit_path.exists():
            write(audit_path, {'reason': 'no_external_original_bodies',
                'original_forecast': task.get('forecast'), 'body_audit': audit,
                'quarantined_at': now(), 'budget_reset': False,
                'terminal_result_preserved': not reopen})
        # A new reader must not retroactively renew a terminal forecast's budget.
        # Explicit legacy repair callers may opt in; normal dispatches only audit.
        if reopen:
            task.update(status='retry_wait', retry_at=None, analysis_stage='analysis-recovered-v1',
                        historical_forecast=task.pop('forecast', None), evidence_status='invalid_original_quarantined')


def run(root, monitor_root, verification_only=False, cfg=None, http=None, collector=acquire, forecaster=None):
    # Native capture children change cwd to the pinned engine. Every request,
    # output and log path passed across that boundary must be absolute.
    root, monitor_root = Path(root).resolve(), Path(monitor_root).resolve()
    cfg = cfg or policy()
    verify(monitor_root)
    source = state(monitor_root, 'monitor', cfg)
    if source['identity']['engine_sha'] != ENGINE_SHA or source['identity']['policy_sha256'] != digest(cfg):
        raise ValueError('Queue pin or policy changed')
    ledger = state(root, 'worker', cfg)
    fresh_source = 0 <= (instant(now()) - instant(source['last_attempt_at'])).total_seconds() <= 3600
    if not fresh_source and any(k not in ledger['tasks'] for k in source['tasks']):
        raise ValueError('Stale discovery cannot enroll unseen tasks')
    # Already enrolled identities can finish from the exact old monitor. Only
    # their fresh price anchor is renewed, never enrollment, creation, or quota.
    for key, task in source['tasks'].items():
        if key not in ledger['tasks']:
            ledger['tasks'][key] = {'key': key, 'market': task['market'], 'status': 'queued',
                                    'executions': 0, 'enrolled_at': task.get('enrolled_at'),
                                    'source_task_sha256': digest(task),
                                    **({'price_schedule': task['price_schedule']} if task.get('price_schedule') else {}),
                                    **({'acquisition_policy': task['acquisition_policy']} if task.get('acquisition_policy') else {}),
                                    **{field: task[field] for field in ('pipeline_version', 'pipeline_commit', 'analysis_version', 'analysis_arms', 'market_search_profile') if field in task}}
        elif ledger['tasks'][key]['source_task_sha256'] != digest(task):
            raise ValueError('Enrolled task identity changed')
        entry = {'key': key, 'enrolled_at': task.get('enrolled_at'),
                 'role': 'immutable_enrollment_quote_not_forecast_anchor',
                 'rules_sha256': task['market']['rules_sha256'],
                 'quote': task['market'].get('last_quote'), 'source_task_sha256': digest(task)}
        entry_path = root / 'tasks' / digest(key) / 'entry-quote.json'
        if entry_path.exists() and read(entry_path) != entry:
            raise ValueError('Frozen enrollment quote changed')
        write(entry_path, entry)
    write(root / 'state.json', ledger)
    quarantine_invalid_results(root, ledger)
    processed = []
    try:
        if not verification_only:
            # A process timeout leaves a running task; resume its exact saved stage.
            pending = pending_keys(ledger)
            http = http or PublicHTTP(root / 'market-http', 15)
            for key in pending[:cfg['maximum_tasks_per_dispatch']]:
                if ledger['tasks'][key]['executions'] >= cfg['maximum_executions_per_task']:
                    ledger['tasks'][key]['status'] = 'blocked'
                    continue
                # Development collection has cumulative call/tool/deadline caps.
                # On Linux, an outer task alarm also bounds non-provider stalls.
                with task_deadline(cfg['maximum_task_runtime_minutes'] * 60):
                    execute(root, ledger, key, cfg, http, collector, forecaster)
                processed.append(key)
        from collections import Counter
        report = {'verification_only': verification_only, 'processed': processed,
                  'state_distribution': dict(Counter(t['status'] for t in ledger['tasks'].values())),
                  'physical_model_reservations': sum(r['kind'] == 'model' for r in ledger.get('provider_reservations', [])),
                  'physical_search_reservations': sum(r['kind'] == 'search' for r in ledger.get('provider_reservations', [])),
                  'engine_sha': ENGINE_SHA, 'engine_branch': ENGINE_BRANCH,
                  'release_fallback': False, 'model': SUPER, 'submission_enabled': False, 'trading_enabled': False}
        report['capture_summaries'] = {k: t['capture_summary'] for k, t in ledger['tasks'].items() if t.get('capture_summary')}
        from .deployment import task_release
        report.update(task_release())
        from .search_credentials import configured
        report['tavily_credentials'] = configured()
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
