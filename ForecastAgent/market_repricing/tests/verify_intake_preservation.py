"""Replay real intake metadata in a private view; never mutate original state."""
import argparse
from collections import Counter
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from ForecastAgent.market_repricing import intake
from ForecastAgent.market_repricing.common import digest, now, policy, read, verify, write
from ForecastAgent.market_repricing.venues import screen


def check(root, output, baseline):
    root = Path(root)
    verified = verify(root)
    original = (root/'state.json').read_bytes()
    saved = read(root/'state.json')
    before = digest({k: saved.get(k) for k in ('tasks', 'daily', 'enrollment_daily', 'provider_reservations')})
    copied = copy.deepcopy(saved)
    spec = importlib.util.spec_from_file_location('ForecastAgent.market_repricing._intake_baseline', baseline)
    old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
    frozen, previous, historical_differences = Counter(), Counter(), []
    for key, task in copied['tasks'].items():
        expected = (task['market'].get('selection') or {}).get('status')
        before_selection = old.screen(task['market'], task['enrolled_at'], policy())
        selection = screen(task['market'], task['enrolled_at'], policy())
        frozen[selection['status']] += 1
        previous[before_selection['status']] += 1
        if before_selection['status'] == 'candidate' and selection['status'] != 'candidate':
            raise ValueError('Previously admitted metadata rejected at its original clock: '+key)
        if expected == 'candidate' and before_selection['status'] != 'candidate':
            historical_differences.append({'key': key, 'frozen_selection': expected,
                'baseline_under_current_policy': before_selection, 'candidate_under_same_policy': selection})
    clock = now()
    for market in copied['markets'].values():
        market['selection'] = screen(market, clock, policy())
    # This independent view has no provider credentials or native execution hook.
    with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'OPENROUTER2': ''}), patch.object(
            intake.decisions, 'decide', side_effect=AssertionError('Unexpected actual provider request')):
        review = intake.review(temp, copied, policy(), clock)
        if review['http_attempts']:
            raise ValueError('Offline preservation attempted a provider request')
        health = intake.health(temp, copied, copied.get('last_scan', {}), clock, len(copied['tasks']))
    after = digest({k: copied.get(k) for k in ('tasks', 'daily', 'enrollment_daily', 'provider_reservations')})
    if before != after or (root/'state.json').read_bytes() != original or verify(root) != verified:
        raise ValueError('Native tasks, budgets or original files changed')
    result = {'schema': 'market-intake-real-state-preservation-v1', 'checked_at': clock,
        'original_files_verified': verified, 'original_state_sha256': hashlib.sha256(original).hexdigest(),
        'native_tasks_preserved': len(copied['tasks']), 'original_admission_clock_statuses': dict(frozen),
        'baseline_admission_clock_statuses': dict(previous), 'new_candidate_rejections': 0,
        'existing_historical_policy_differences': historical_differences,
        'baseline_source_sha256_lf': hashlib.sha256(Path(baseline).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        'current_selection_distribution': dict(Counter(m['selection']['status'] for m in copied['markets'].values())),
        'offline_review_status': review['status'], 'intake_health': health,
        'original_files_unchanged': True, 'native_budgets_unchanged': True,
        'provider_calls': 0, 'tasks_reopened': False, 'forecasts_submitted': False}
    write(output, result)
    print(__import__('json').dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    check(args.root, args.output, args.baseline)
