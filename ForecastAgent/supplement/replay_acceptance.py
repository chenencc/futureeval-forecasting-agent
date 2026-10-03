"""Offline replay of all saved pilot captures, inheriting every consumed attempt."""
import argparse
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.supplement.enhanced import run, assess, plan


def replay(parent, output):
    parent, output = map(Path, (parent, output)); rows = []
    for folder in sorted((parent/'tasks').iterdir()):
        if not (folder/'candidate/state.json').exists(): continue
        bundle = load(folder/'candidate/analysis-input.json')
        prior = [load(folder/'prior-supplement.json'), load(folder/'candidate/state.json')]
        before_usage = load(folder/'candidate/report.json')['usage']
        view = run(bundle, output/'tasks'/folder.name, prior=prior, network=False)
        after_usage = load(output/'tasks'/folder.name/'report.json')['usage']
        if before_usage != after_usage: raise ValueError('Offline replay changed lifetime accounting')
        comparisons = []
        for attempt in prior[1]['attempts']:
            if attempt.get('response_file'):
                page = load(folder/'candidate'/attempt['response_file'])
                if digest(page) != attempt['response_sha256']: raise ValueError('Original capture changed')
                fresh = assess(bundle['request'], attempt['url'], page.get('content', ''))
                comparisons.append({'url': attempt['url'], 'old': attempt['assessment']['coverage_status'],
                    'new': fresh['coverage_status'], 'source_contract': fresh['source_contract'],
                    'raw_body_sha256_unchanged': True})
        rows.append({'id': folder.name, 'cumulative_usage_unchanged': True, 'usage': after_usage,
                     'comparisons': comparisons, 'proposed_sources': plan(view)['sources'],
                     'same_original_pages_preserved': set(bundle['pages']) <= set(view['pages'])})
    save(output/'report.json', {'protocol': 'source-contract-offline-replay-v1', 'rows': rows,
         'network_calls': 0, 'model_calls': 0, 'budget_reset': False,
         'coverage_diagnostics_are_not_truth_verification': True})
    return rows


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--parent', required=True); p.add_argument('--output', required=True)
    args = p.parse_args(); replay(args.parent, args.output)
