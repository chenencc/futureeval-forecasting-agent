"""Resume quota-blocked routes without changing frozen inputs or lifetime caps."""
import argparse
import hashlib
import os
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis import enhanced_pair40 as trial
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import Journal, digest, load, save
from ForecastAgent.providers import decisions


def resumed_call(state, folder, decision_questions=None):
    folder = Path(folder)
    registry = decision_questions or chain.questions()
    request = {'model': decisions.MODEL, 'state': state, 'questions': registry}
    identity = {'request_sha256': digest(request)}
    if (folder/'identity.json').exists() and load(folder/'identity.json') != identity:
        raise ValueError('Frozen decision stage changed')
    if (folder/'response.json').exists():
        return decisions.validate(load(folder/'response.json'), registry)
    prior = sorted((folder/'http').glob('*.json'))
    if prior:
        last = load(prior[-1])
        if last.get('status') != 'http_error' or last.get('http_status') != 429:
            raise RuntimeError('Only explicitly recorded HTTP 429 stages may retry')
    route_root = folder.parent
    if len(list(route_root.glob('*/http/*.json'))) >= 2:
        raise RuntimeError('Route lifetime HTTP attempt cap exhausted')
    save(folder/'identity.json', identity)
    save(folder/'request.json', request)
    journal = Journal(folder/'http', 2)

    def observer(phase, record, token=None):
        if phase == 'reserve' and len(list(route_root.glob('*/http/*.json'))) >= 2:
            raise RuntimeError('Route lifetime HTTP attempt cap exhausted')
        return journal(phase, record, token)

    response = decisions.decide(state, registry, os.environ['OPENROUTER_API_KEY'], observer)
    save(folder/'response.json', response)
    return response


def run(batch, output, parent_run):
    output = Path(output)
    if not (output/'identity.json').exists() or not (output/'report.json').exists():
        raise ValueError('Exact parent artifact required for continuation')
    original_route = trial.route
    preserved = {}
    for result in output.glob('tasks/*/*/result.json'):
        preserved[str(result.relative_to(output))] = hashlib.sha256(result.read_bytes()).hexdigest()
    before = len(list(output.glob('tasks/*/*/*/http/*.json')))
    save(output/'credential-continuation.json', {
        'parent_run': str(parent_run), 'secret_name': 'OPENROUTER2',
        'http_attempts_before': before, 'http_cap_per_route': 2,
        'quota_reset': False, 'frozen_completed_result_sha256': preserved,
        'retry_policy': 'Only HTTP 429, using remaining lifetime route allowance',
    })

    def cached_route(item, name, folder, dry_run=False):
        path = Path(folder)/'result.json'
        if path.exists():
            return load(path)
        return original_route(item, name, folder, dry_run)

    try:
        with patch.object(trial, 'route', cached_route), patch.object(chain, 'call', resumed_call):
            trial.run(batch, output)
    finally:
        for path, expected in preserved.items():
            if hashlib.sha256((output/path).read_bytes()).hexdigest() != expected:
                raise ValueError('Completed result changed during continuation')
        after = len(list(output.glob('tasks/*/*/*/http/*.json')))
        save(output/'continuation-audit.json', {
            'http_attempts_before': before, 'http_attempts_after': after,
            'new_http_attempts': after-before, 'completed_results_unchanged': True,
            'search_requests': 0, 'forecast_submissions': 0,
        })


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=int, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--parent-run', required=True)
    args = parser.parse_args()
    run(args.batch, args.output, args.parent_run)
