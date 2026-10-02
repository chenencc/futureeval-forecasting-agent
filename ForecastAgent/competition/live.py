"""Official deadline-ordered acquisition, analysis and atomic submission worker."""
import argparse
import copy
import json
import os
import shutil
import time
import zipfile
from datetime import timedelta
from pathlib import Path

from ForecastAgent.analysis.distributions import POLICY, payload
from ForecastAgent.competition.nonbinary_debug import blind_input
from ForecastAgent.competition.platform import Client, deliver
from ForecastAgent.competition.queue import descriptor, digest, load, questions, save, utc
from ForecastAgent.runtime.task_lock import task_lock

SCHEMA = 'official-competition-v1'
TERMINAL = {'accepted', 'closed', 'deadline_missed', 'blocked_integrity', 'provider_blocked', 'platform_rejected'}


def live_request(post, question):
    if question['type'] != 'binary':
        request = blind_input(post, question['id'])
    else:
        criteria = question.get('resolution_criteria') or post.get('resolution_criteria')
        if not isinstance(criteria, str) or not criteria.strip():
            raise ValueError('Official resolution criteria missing')
        request = {'id': str(question['id']), 'question': question.get('title') or post['title'],
            'question_type': 'binary', 'resolution_criteria': criteria,
            'fine_print': question.get('fine_print') or post.get('fine_print') or '',
            'background': question.get('description') or post.get('description') or '',
            'mode': 'live', 'pipeline': 'collection', 'acquisition_profile': 'collection_v3'}
    request['official_competition'] = True
    request.update({key: question.get(key) for key in ('open_time', 'close_time',
        'scheduled_close_time', 'scheduled_resolve_time', 'spot_scoring_time')})
    return request


def rule_identity(post, question):
    fields = descriptor(post, question)
    return digest({k: fields.get(k) for k in ('title', 'type', 'resolution_criteria', 'fine_print')} |
        {k: question.get(k) for k in ('options', 'scaling', 'inbound_outcome_count', 'open_lower_bound', 'open_upper_bound', 'unit')})


def current(client, task, *, allow_closed_receipt=False):
    post = client.post(task['post_id'])
    question = next((q for q in questions(post) if str(q['id']) == task['id']), None)
    if question is None:
        raise ValueError('Live question identity missing')
    if not allow_closed_receipt and (question.get('status') != 'open' or post.get('user_permission') not in ('forecaster', 'curator', 'admin', 'creator')):
        raise RuntimeError('Question is closed or forecasting permission unavailable')
    if not allow_closed_receipt and rule_identity(post, question) != task['rule_identity']:
        raise ValueError('Official question rules or distribution metadata changed')
    desc = descriptor(post, question)
    if not allow_closed_receipt and (not desc['deadline_utc'] or utc(desc['deadline_utc']) <= utc()):
        raise RuntimeError('Forecasting deadline has passed')
    task['deadline_utc'] = desc['deadline_utc']
    return post, question


def analyze(bundle_path, folder, ident):
    bundle = load(bundle_path)
    kind = bundle['request']['question_type']
    if kind == 'binary':
        from ForecastAgent.analysis.referenced import run
        base = folder / 'binary-input'
        save(base / 'tasks' / ident / 'bundle.json', bundle)
        save(base / 'campaign.json', {'schema': SCHEMA, 'tasks': {ident: {'status': 'acquired'}}})
        try:
            run(base, folder / 'analysis', [ident], mode='both', live=True)
        except RuntimeError:
            result_path = folder / 'analysis' / 'tasks' / ident / 'result.json'
            if not result_path.exists() or load(result_path).get('reasoning_probability_yes') is None:
                raise
        result = load(folder / 'analysis' / 'tasks' / ident / 'result.json')
        reasoning = result.get('reasoning_probability_yes')
        mercury = result.get('mercury_probability_yes')
        if reasoning is None:
            raise RuntimeError('No valid reasoning output; refuse an uninformed default')
        selected = (reasoning + mercury) / 2 if mercury is not None else reasoning
        raw = {'probability_yes': selected}
        report = load(folder / 'analysis' / 'tasks' / ident / 'analysis.json')
    else:
        if kind == 'multiple_choice':
            from ForecastAgent.analysis.categorical import run
        else:
            from ForecastAgent.analysis.range_forecast import run
        result = run(bundle_path, folder / 'analysis')
        raw = result['payload_preview']
        report = load(folder / 'analysis' / 'analysis.json')
        mercury = result.get('mercury_probabilities') if kind == 'multiple_choice' else result.get('mercury_cdf')
    candidate = payload(bundle['request'], raw)
    selected_route = 'equal_mean' if mercury is not None else 'single_available_reasoning_route'
    # All text comes from the automatic saved analysis, not an operator forecast.
    comment = '# ForecastAgent 1.0 competition\n\n' + '\n\n'.join(
        f'## {key.replace("_", " ").title()}\n{json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value}'
        for key, value in report.items() if key not in ('reasoning_probability_yes', 'probabilities', 'quantiles', 'below_lower_bound', 'above_upper_bound'))
    comment += '\n\n## Delivery policy\n' + selected_route + '; ' + POLICY + '.\n\n## Forecast\n' + json.dumps(candidate)
    save(folder / 'candidate.json', {'payload': candidate, 'selection': selected_route,
        'model_result_sha256': digest(result), 'comment': comment, 'automatic': True})
    return load(folder / 'candidate.json')


def supplement_bundle(bundle, folder, ident):
    from ForecastAgent.supplement.stage import run, analysis_overlay
    archive = folder / 'supplement-input.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('campaign.json', json.dumps({'tasks': {ident: {'status': 'acquired'}}}))
        z.writestr(f'tasks/{ident}/bundle.json', json.dumps(bundle))
    run(archive, folder / 'supplement', [ident], network=True)
    return analysis_overlay(bundle, folder / 'supplement', ident)


def run(root, snapshot_root, *, enabled=False, legacy_root=None, limit=5, client=None,
        collect=None, supplement=None, infer=None, deliver_fn=None):
    if not 1 <= limit <= 5:
        raise ValueError('At most five questions per dispatch')
    client = client or Client(os.environ.get('METACULUS_TOKEN', ''))
    account = client.account()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        state_path = root / 'campaign.json'
        if not state_path.exists() and (root / 'tasks').exists():
            raise ValueError('Official task state exists without ledger; refuse quota restart')
        state = load(state_path) if state_path.exists() else {'schema': SCHEMA, 'tournament': 'fall-futureeval-2026', 'tasks': {}}
        if state['schema'] != SCHEMA or state['tournament'] != 'fall-futureeval-2026':
            raise ValueError('Wrong official campaign identity')
        save(root / 'account-check.json', account)
        indexes = sorted(Path(snapshot_root).rglob('index.json'))
        indexes = [p for p in indexes if load(p).get('tournament') == state['tournament']]
        if not indexes:
            raise ValueError('No tournament snapshot found')
        index_path = max(indexes, key=lambda p: load(p)['retrieved_at_utc'])
        index = load(index_path)
        if not index.get('open_scan_complete'):
            raise ValueError('Incomplete open scan')
        if not 0 <= (utc() - utc(index['retrieved_at_utc'])).total_seconds() <= 7200:
            raise ValueError('Incoming official snapshot is stale')
        for row in index['questions']:
            if not row.get('open'):
                continue
            ident = str(row['question_id'])
            if not ident.isdecimal() or type(row['post_id']) is not int:
                raise ValueError('Invalid incoming question identity')
            if ident not in state['tasks']:
                state['tasks'][ident] = {'id': ident, 'post_id': str(row['post_id']), 'stage': 'queued',
                    'discovered_at_utc': utc().isoformat(), 'collection_executions': 0,
                    'commit': os.environ.get('GITHUB_SHA'), 'history': []}
                snapshot = load(index_path.parent / f"{row['post_id']}.json")['post']
                q = next((q for q in questions(snapshot) if str(q['id']) == ident), None)
                if snapshot['id'] != row['post_id'] or q is None:
                    raise ValueError('Incoming post/question identity mismatch')
                state['tasks'][ident]['deadline_utc'] = descriptor(snapshot, q)['deadline_utc']
        save(state_path, state)
        pending = [t for t in state['tasks'].values() if t['stage'] not in TERMINAL and
            (not t.get('retry_at_utc') or utc(t['retry_at_utc']) <= utc())]
        pending.sort(key=lambda t: t.get('deadline_utc') or index['retrieved_at_utc'])
        processed = []
        problems = []
        dispatch_start = time.monotonic()
        for task in pending[:limit]:
            if time.monotonic() - dispatch_start > 3000:
                break
            ident = task['id']
            folder = root / 'tasks' / ident
            folder.mkdir(parents=True, exist_ok=True)
            try:
                # Reconcile interrupted deliveries even after the question closes.
                if (folder / 'submission.json').exists():
                    candidate = load(folder / 'candidate.json')
                    receipt = (deliver_fn or deliver)(client, task, candidate['payload'], candidate['comment'], folder, enabled=enabled)
                    task['stage'] = receipt['status']
                    save(state_path, state)
                    continue
                post = client.post(task['post_id'])
                question = next((q for q in questions(post) if str(q['id']) == ident), None)
                if question is None:
                    raise ValueError('Question missing from its registered post')
                desc = descriptor(post, question)
                task['deadline_utc'] = desc['deadline_utc']
                if question.get('status') != 'open':
                    task['stage'] = 'closed'
                    save(state_path, state)
                    continue
                if not desc['deadline_utc'] or utc(desc['deadline_utc']) <= utc():
                    task['stage'] = 'deadline_missed'
                    save(state_path, state)
                    continue
                identity = rule_identity(post, question)
                if task.get('rule_identity') and task['rule_identity'] != identity:
                    raise ValueError('Frozen live rules changed')
                task['rule_identity'] = identity
                request = live_request(post, question)
                if not enabled:
                    task['stage'] = 'queued'
                    save(state_path, state)
                    continue
                current(client, task)
                if (folder / 'candidate.json').exists():
                    candidate = load(folder / 'candidate.json')
                else:
                    retrieval = folder / 'retrieval'
                    prior = Path(legacy_root) / 'retrieval' / ident if legacy_root else None
                    if prior and (prior / 'bundle.json').exists() and not retrieval.exists():
                        prior_bundle = load(prior / 'bundle.json')
                        if prior_bundle['request'].get('mode') != 'live':
                            raise ValueError('Refuse historical evidence as live acquisition')
                        if any((prior_bundle['request'].get(k) or '') != (request.get(k) or '') for k in ('question', 'resolution_criteria', 'fine_print')):
                            raise ValueError('Existing live collector rules disagree; preserve original budgets')
                        shutil.copytree(prior, retrieval)
                        legacy_state = load(Path(legacy_root) / 'state.json') if (Path(legacy_root) / 'state.json').exists() else {}
                        recorded = legacy_state.get('research_retries', {}).get(ident, {}).get('attempts', 0)
                        task['collection_executions'] = max(1, len(prior_bundle.get('sessions', [])), recorded)
                        task['adopted_legacy_bundle_sha256'] = digest(prior_bundle)
                        save(state_path, state)
                    bundle_path = retrieval / 'bundle.json'
                    bundle = load(bundle_path) if bundle_path.exists() else None
                    if not bundle or not bundle.get('result') or bundle['result'].get('incomplete'):
                        if task['collection_executions'] >= 3:
                            raise RuntimeError('Collection lifetime execution cap exhausted')
                        task['collection_executions'] += 1
                        task['stage'] = 'collecting'
                        save(state_path, state)
                        if collect is None:
                            from ForecastAgent.agent import run_research
                            collector = run_research
                        else:
                            collector = collect
                        bundle = collector(bundle['request'] if bundle else request, retrieval)
                    if not bundle.get('result') or bundle['result'].get('incomplete'):
                        raise RuntimeError('Collection interrupted; original budgets preserved')
                    current(client, task)
                    adopted = copy.deepcopy(bundle)
                    adopted['request'].update(request)
                    source = folder / 'supplement-source.json'
                    if source.exists():
                        adopted = load(source)
                    else:
                        adopted['request']['as_of_utc'] = utc().isoformat()
                        save(source, adopted)
                    frozen = folder / 'analysis-input.json'
                    if not frozen.exists():
                        task['stage'] = 'supplementing'
                        save(state_path, state)
                        overlay = (supplement or supplement_bundle)(adopted, folder, ident)
                        save(frozen, overlay)
                    current(client, task)
                    task['stage'] = 'analyzing'
                    save(state_path, state)
                    candidate = (infer or analyze)(frozen, folder, ident)
                current(client, task)
                receipt = (deliver_fn or deliver)(client, task, candidate['payload'], candidate['comment'], folder, enabled=True)
                task.update(stage=receipt['status'], retry_at_utc=None, accepted_at_utc=receipt.get('confirmed_at_utc'))
            except Exception as exc:
                message = str(exc)
                stage = 'blocked_integrity' if isinstance(exc, ValueError) else 'retry_wait'
                if (folder / 'submission.json').exists():
                    stage = 'platform_rejected' if load(folder / 'submission.json')['status'] == 'rejected' else 'submission_unknown'
                elif 'cap exhausted' in message.lower() or 'quota' in message.lower():
                    stage = 'provider_blocked'
                elif 'closed' in message.lower() or 'deadline' in message.lower():
                    stage = 'deadline_missed'
                task.update(stage=stage, last_error=type(exc).__name__ + ': ' + message,
                    retry_at_utc=(utc() + timedelta(minutes=10)).isoformat())
                save(folder / 'failure.json', {'stage': stage, 'error': task['last_error'], 'preserved_state': True})
                problems.append({'id': ident, 'stage': stage, 'error': task['last_error']})
            finally:
                task['history'].append({'at_utc': utc().isoformat(), 'stage': task['stage']})
                processed.append(ident)
                save(state_path, state)
        counts = {}
        for task in state['tasks'].values():
            counts[task['stage']] = counts.get(task['stage'], 0) + 1
        report = {'schema': SCHEMA, 'commit': os.environ.get('GITHUB_SHA'), 'enabled': enabled,
            'snapshot_at_utc': index['retrieved_at_utc'], 'open_question_count': index.get('open_question_count'),
            'state_distribution': counts, 'processed_ids': processed, 'problems': problems,
            'selection_policy': 'Mean if reasoning and Mercury exist; otherwise reasoning only',
            'probability_policy': POLICY, 'finished_at_utc': utc().isoformat()}
        save(root / 'report.json', report)
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--snapshots', required=True)
    parser.add_argument('--legacy-root')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    report = run(args.root, args.snapshots, enabled=args.submit,
        legacy_root=args.legacy_root, limit=args.limit)
    print(json.dumps(report, indent=2))
    if report['problems']:
        raise SystemExit(1)
