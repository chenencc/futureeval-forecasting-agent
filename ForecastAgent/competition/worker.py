"""Bounded shadow orchestration over the existing collector and v8 journals."""
from datetime import timedelta
from pathlib import Path

from ForecastAgent.runtime.task_lock import task_lock
from .bridge import archive_input, freeze, validate_frozen
from .queue import Queue, descriptor, digest, load, questions, save, utc


def current_question(task, token):
    from ForecastAgent.monitor_tournament import get_json, API_ROOT
    post = get_json(f"{API_ROOT}{task['question']['post_id']}/", token)
    matches = [q for q in questions(post) if str(q['id']) == task['id']]
    if len(matches) != 1:
        raise ValueError('Live API question identity mismatch')
    return descriptor(post, matches[0])


def drain(root, collector_root, *, execute=False, token='', limit=5,
          network_supplement=False, safety_seconds=1800, now=None,
          refresh=None, analyze=None, supplement=None):
    """Dry mode only reports candidates. Execution requires a live status check."""
    if not 1 <= limit <= 5 or safety_seconds < 1800:
        raise ValueError('Select at most five tasks and reserve at least 30 minutes')
    if execute and not token and refresh is None:
        raise ValueError('Live API recheck requires METACULUS_TOKEN')
    if now is not None and execute and refresh is None:
        raise ValueError('Production execution must use the current clock')
    clock = lambda: utc(now).isoformat()
    Path(root).mkdir(parents=True, exist_ok=True)
    with task_lock(Path(root)):
        queue = Queue(root)
        pending = queue.pending(clock())
        tasks = [task for task in pending if
                 (Path(root) / 'tasks' / task['id'] / 'input' / 'bridge.json').exists() or
                 (Path(collector_root) / 'retrieval' / task['id'] / 'bundle.json').exists()][:limit]
        if not execute:
            report = {**queue.report(), 'pending_ids': [t['id'] for t in tasks], 'mode': 'dry'}
            save(Path(root) / 'report.json', report)
            return report
        if analyze is None:
            from ForecastAgent.analysis.referenced import run as analyze
        if supplement is None:
            from ForecastAgent.supplement.stage import run as supplement
        refresh = refresh or (lambda task: current_question(task, token))

        def guard(task, reserve=True):
            latest = refresh(task)
            if latest['rule_sha256'] != task['question']['rule_sha256']:
                raise ValueError('Live question rule identity changed')
            task['last_api_check_utc'] = clock()
            if latest['status'] != 'open':
                queue.event(task, 'closed', 'Live API reports a non-open question', clock())
                queue.commit()
                return False
            if not latest['deadline_utc']:
                raise ValueError('Live question has no scoring deadline')
            task['question'] = latest
            remaining = (utc(latest['deadline_utc']) - utc(clock())).total_seconds()
            if remaining <= 0:
                queue.event(task, 'deadline_missed', 'Live deadline passed', clock())
                queue.commit()
                return False
            if reserve and remaining < safety_seconds:
                queue.event(task, 'deadline_at_risk', 'Insufficient time reserved for inference; no new calls', clock())
                queue.commit()
                return False
            queue.commit()
            return True

        for task in tasks:
            ident = task['id']
            folder = Path(root) / 'tasks' / ident
            try:
                if not guard(task):
                    continue
                frozen = folder / 'input'
                if (frozen / 'bridge.json').exists():
                    marker = validate_frozen(frozen)
                    if marker['rule_sha256'] != task['question']['rule_sha256']:
                        raise ValueError('Frozen question rule identity mismatch')
                else:
                    if freeze(task, collector_root, frozen, clock()) is None:
                        continue
                frozen_hash = digest(load(frozen / 'bridge.json'))
                if task.get('frozen_input_sha256') and task['frozen_input_sha256'] != frozen_hash:
                    raise ValueError('Queued frozen input identity changed')
                task['frozen_input_sha256'] = frozen_hash
                task['retry_at_utc'] = None
                queue.event(task, 'supplementing', 'Use bounded supplemental journal; no search calls', clock())
                queue.commit()
                supplement(archive_input(frozen), folder / 'supplement', [ident], network=network_supplement)
                if not guard(task):
                    continue
                queue.event(task, 'analyzing', 'Frozen release 1.0 diagnostic with durable model journals', clock())
                task['attempts'] += 1
                queue.commit()
                analyze(frozen, folder / 'analysis', [ident], str(folder / 'supplement'), 'both')
                # A completed response is preserved even if the deadline changed mid-call.
                if not guard(task, reserve=False):
                    continue
                result = load(folder / 'analysis' / 'tasks' / ident / 'result.json')
                if result.get('status') not in ('completed', 'partial', 'provisional'):
                    raise RuntimeError('Analysis has no available model output')
                routes = load(folder / 'analysis' / 'tasks' / ident / 'routes.json')
                probability = result.get('equal_mean_probability_yes')
                origin = 'equal_mean'
                if probability is None:
                    probability = result.get('reasoning_probability_yes')
                    origin = 'single_available_reasoning_route'
                if type(probability) not in (int, float) or not 0 <= probability <= 1:
                    raise ValueError('Invalid model probability; no operational default permitted')
                from ForecastAgent.analysis.distributions import clip_probability, POLICY
                candidate = {'schema': 'competition-shadow-candidate-v1', 'id': ident,
                    'probability_yes': clip_probability(probability), 'raw_probability_yes': probability,
                    'probability_policy': POLICY, 'selection': origin, 'analysis_status': result['status'],
                    'quality': routes.get('quality'), 'created_at_utc': clock(),
                    'frozen_input_sha256': task['frozen_input_sha256'], 'routes_sha256': digest(routes),
                    'deadline_utc': task['question']['deadline_utc'],
                    'automatic_submission_enabled': False, 'historical_analysis_prompt': True,
                    'no_forecasts_submitted': True}
                save(folder / 'candidate.json', candidate)
                task['candidate_sha256'] = digest(candidate)
                queue.event(task, 'shadow_ready', 'Diagnostic candidate saved; submission is disabled', clock())
                queue.commit()
            except Exception as exc:
                reason = f'{type(exc).__name__}: {exc}'
                failure = folder / 'analysis' / 'tasks' / ident / 'failure.json'
                if failure.exists():
                    reason += '; preserved analysis: ' + str(load(failure).get('error'))
                task['last_error'] = reason
                if isinstance(exc, ValueError):
                    stage = 'blocked_integrity'
                elif 'cap exhausted' in reason.lower() or 'quota' in reason.lower():
                    stage = 'provider_blocked'
                else:
                    stage = 'retry_wait'
                    task['retry_at_utc'] = (utc(clock()) + timedelta(minutes=20)).isoformat()
                queue.event(task, stage, reason, clock())
                queue.commit()
        report = {**queue.report(), 'mode': 'shadow', 'processed_ids': [t['id'] for t in tasks]}
        save(Path(root) / 'report.json', report)
        return report
