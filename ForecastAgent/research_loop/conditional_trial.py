"""Frozen source-first map plus one batched direct/conditional Mercury request."""
import argparse
import copy
import json
from pathlib import Path
import os

from ForecastAgent.analysis.pilot import load, save, WARNING
from ForecastAgent.research_loop import POLICY, conditional, decision, live_trial, state
from ForecastAgent.runtime.task_lock import task_lock


def receipts(root):
    root = Path(root)
    return list(root.glob('cases/*/map/http/*.json')) + list(root.glob('cases/*/score/decision/http/*.json'))


def run(parents, root, *, execute=False, max_http=10, previous_budget=None):
    """Failed cases remain in the report; maps and requests never renew on resume."""
    if not 1 <= len(parents) <= 5 or type(max_http) is not int or max_http < 0:
        raise ValueError('Use one to five explicit cases and a nonnegative physical HTTP cap')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    frozen, ids = [], []
    for file in parents:
        file = Path(file).resolve()
        bundle = load(file)
        ident = str(bundle['request']['id'])
        if ident in ids:
            raise ValueError('Duplicate question ID')
        ids.append(ident)
        frozen.append({'id': ident, 'input': str(file), 'sha256': live_trial.sha(file)})
    prior = None
    if previous_budget:
        source = Path(previous_budget).resolve()
        report = load(source)
        used, cap = report['usage']['cumulative_http'], report['usage']['authorized_cap']
        if type(used) is not int or type(cap) is not int or max_http > cap-used:
            raise ValueError('New trial would exceed the preserved campaign HTTP cap')
        prior = {'source': str(source), 'sha256': live_trial.sha(source), 'used': used, 'cap': cap}
    identity = {'protocol': conditional.SCORING_PROTOCOL, 'cases': frozen,
        'implementation': decision.implementation_hashes(), 'max_http': max_http,
        'previous_budget': prior, 'super_http_cap': 1, 'mercury_http_cap': 1,
        'primary_policy': 'direct', 'thresholds': [conditional.DISAGREEMENT, conditional.MIN_PIVOT_USABILITY],
        'submitted': False, 'searches': 0, 'fetches': 0, 'quota_reset': False,
        'evaluation_warning': WARNING}
    with task_lock(root):
        if (root/'identity.json').exists() and load(root/'identity.json') != identity:
            raise ValueError('Frozen trial identity or cumulative budget changed')
        save(root/'identity.json', identity)
        rows = []
        for entry in frozen:
            if live_trial.sha(entry['input']) != entry['sha256']:
                raise ValueError('Original source changed')
            original = load(entry['input']); child = copy.deepcopy(original)
            child['request']['research_state_policy'] = POLICY
            child['pipeline'] = 'collection'; state.initialize(child)
            prepared = decision.prepare(child)
            folder = root/'cases'/entry['id']
            save(folder/'common-original-state.json', prepared['baseline'])
            row = {'id': entry['id'], 'question': original['request']['question'],
                'question_type': original['request']['question_type'], 'status': 'prepared'}
            if execute:
                try:
                    map_cached = (folder/'map/accepted-state.json').exists()
                    if not map_cached and len(receipts(root)) + 2 > max_http:
                        raise RuntimeError('Insufficient room for map and final score; reserve the last attempt for direct scoring')
                    child = live_trial.map_stage(child, prepared['baseline'], folder/'map',
                        os.environ['OPENROUTER_API_KEY'], http_cap=1, map_protocol=conditional.PROTOCOL)
                    save(folder/'research-package.json', child)
                    row['map_status'] = 'completed'
                    row['plan_audit'] = child['research_loop']['events'][-1]['acceptance']['score_plan']
                except Exception as exc:
                    # A failed map is not a failed forecast if direct scoring remains possible.
                    row['map_error'] = type(exc).__name__+': '+str(exc)
                try:
                    score_cached = (folder/'score/decision/response.json').exists()
                    if not score_cached and len(receipts(root)) >= max_http:
                        raise RuntimeError('Preserved total HTTP cap exhausted before score')
                    result = conditional.run(child, folder/'score', execute=True)
                    row.update(status='completed', direct=live_trial.forecast_summary(result, original['request']),
                        conditional=result['conditional'], review_required=result['review_required'],
                        audit=result['audit'], primary_policy='direct')
                except Exception as exc:
                    row.update(status='failed', score_error=type(exc).__name__+': '+str(exc))
            row['preserved'] = original['pages'] == child['pages'] and all(original.get(k) == child.get(k)
                for k in ('searches', 'exa_searches', 'fetch_attempts', 'model_attempts', 'extract_attempts'))
            row['source_preserved'] = live_trial.sha(entry['input']) == entry['sha256']
            if not row['preserved'] or not row['source_preserved']:
                raise ValueError('Original material or acquisition ledgers changed')
            save(folder/'result.json', row); rows.append(row)
            usage = live_trial.usage(receipts(root))
            if usage['totals']['http_attempts'] > max_http:
                raise ValueError('Physical HTTP cap exceeded')
            report = {'schema': conditional.SCORING_PROTOCOL, 'execute': execute, 'cases': rows,
                'requested_cases': len(frozen), 'processed_cases': len(rows),
                'completed': sum(r['status'] == 'completed' for r in rows),
                'usage': usage, 'cumulative_http': (prior['used'] if prior else 0) + len(receipts(root)),
                'authorized_http_cap': prior['cap'] if prior else max_http,
                'searches': 0, 'fetches': 0, 'submitted': False, 'quota_reset': False,
                'primary_policy': 'direct', 'accuracy': None, 'brier': None,
                'evaluation': 'Archived regression with the same originals; no labels read or calibration claimed'}
            save(root/'report.json', report)
            print(json.dumps({'id': entry['id'], 'status': row['status'],
                'conditional_status': row.get('conditional', {}).get('status'),
                'new_http': len(receipts(root))}), flush=True)
        return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parents', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--max-http', type=int, default=10)
    p.add_argument('--previous-budget', type=Path)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    run(load(args.parents), args.root, execute=args.execute,
        max_http=args.max_http, previous_budget=args.previous_budget)


if __name__ == '__main__':
    main()
