"""Run frozen saved-body cohorts in isolated local processes and retain every case."""
import argparse
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.research_loop import decision, POLICY
from ForecastAgent.research_loop.live_trial import SUPER, SUPER_HTTP_CAP, sha, visible_catalog
from ForecastAgent.research_loop.state import now, initialize
from ForecastAgent.runtime.task_lock import task_lock


def freeze(cohort, root, workers):
    """Validate explicit membership before any provider call; never select by outcome."""
    if not 1 <= workers <= 3:
        raise ValueError('Use one to three isolated workers')
    rows = cohort['cases']
    ids = [str(row['id']) for row in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError('Cohort must contain unique question IDs')
    frozen = []
    for row in rows:
        path = Path(row['input']).resolve()
        if sha(path) != row['input_sha256']:
            raise ValueError('Frozen input hash changed: ' + str(row['id']))
        bundle = load(path)
        if str(bundle['request']['id']) != str(row['id']):
            raise ValueError('Question ID does not match its frozen input')
        # Match live_trial initialization exactly, including the question policy.
        # The archived parent is immutable; only the experimental copy changes.
        child = copy.deepcopy(bundle)
        child['request']['research_state_policy'] = POLICY
        child['pipeline'] = 'collection'
        initialize(child)
        prepared = decision.prepare(child)
        visible_catalog(child, prepared['baseline'])
        frozen.append({**copy.deepcopy(row), 'input': str(path),
            'common_original_state_sha256': prepared['audit']['baseline_state_sha256'],
            'preflight': 'passed'})
    groups = [frozen[i::workers] for i in range(workers)]
    identity = {'schema': 'frozen-local-research-cohort-v1', 'cases': frozen,
        'implementation': decision.implementation_hashes(), 'workers': workers,
        'super_model': SUPER, 'super_http_cap_per_case': SUPER_HTTP_CAP,
        'mercury_model': 'inception/mercury-decide:free', 'mercury_http_cap_per_arm': 1,
        'search_calls': 0, 'fetch_calls': 0, 'submission_enabled': False,
        'label_policy': 'No outcome labels or previous forecasts supplied to either arm',
        'timing_policy': 'New inference on archived evidence; never backdate the forecast',
        'comparison': 'Fresh A and B on identical selected original evidence; earlier release output is a separate reference',
        'shards': [[row['id'] for row in group] for group in groups]}
    identity_path = Path(root)/'cohort-identity.json'
    if identity_path.exists() and load(identity_path) != identity:
        raise ValueError('Frozen cohort, model policy, implementation or worker count changed')
    save(identity_path, identity)
    for index, group in enumerate(groups):
        save(Path(root)/'shards'/str(index)/'parents.json', [row['input'] for row in group])
    return identity


def aggregate(root, identity, *, finished=False, exits=None, execute=True):
    root = Path(root)
    metadata = {str(row['id']): row for row in identity['cases']}
    cases, shards = [], []
    for index in range(identity['workers']):
        folder = root/'shards'/str(index)
        progress = load(folder/'progress.json') if (folder/'progress.json').exists() else {}
        shard = {'index': index, 'progress': progress,
                 'exit_code': (exits or {}).get(index)}
        if (folder/'report.json').exists():
            report = load(folder/'report.json')
            shard['processed_cases'] = report['processed_cases']
            for case in report['cases']:
                row = metadata[case['id']]
                cases.append({**case, 'stream': row['stream'],
                    'canonical_post_id': row.get('canonical_post_id'),
                    'archived_as_of_utc': row.get('archived_as_of_utc'),
                    'directory': str(folder/'cases'/case['id']),
                    'previous_release_reference': row.get('previous_release_reference')})
        shards.append(shard)
    order = {str(row['id']): i for i, row in enumerate(identity['cases'])}
    cases.sort(key=lambda c: order[c['id']])
    if len(cases) != len({c['id'] for c in cases}):
        raise ValueError('Duplicate question results across isolated shards')
    processed = {c['id'] for c in cases}
    totals = {}
    for stage in ('super', 'baseline', 'enriched'):
        counts = {}
        for case in cases:
            for key, value in case[stage+'_usage']['totals'].items():
                counts[key] = counts.get(key, 0) + value
        totals[stage] = counts
    distributions = {}
    for stream in sorted({r['stream'] for r in identity['cases']}):
        selected = [c for c in cases if c['stream'] == stream]
        distributions[stream] = {
            'requested': sum(r['stream'] == stream for r in identity['cases']),
            'processed': len(selected),
            'paired_completed': sum(c['status'] == 'paired_completed' for c in selected),
            'baseline_completed': sum(c.get('baseline', {}).get('status') == 'completed' for c in selected),
            'enriched_completed': sum(c.get('enriched', {}).get('status') == 'completed' for c in selected)}
    report = {'schema': identity['schema'], 'at_utc': now(),
        'status': ('finished' if execute else 'prepared') if finished and len(cases) == len(metadata) else 'incomplete' if finished else 'running',
        'execute': execute,
        'requested_cases': len(metadata), 'processed_cases': len(cases),
        'unprocessed_ids': [i for i in order if i not in processed],
        'paired_completed': sum(c['status'] == 'paired_completed' for c in cases),
        'streams': distributions, 'usage': totals, 'shards': shards, 'cases': cases,
        'submitted': False, 'no_new_retrieval': True, 'accuracy_metrics': None,
        'evaluation_status': 'Await independently archived official resolutions and timing eligibility checks'}
    save(root/'report.json', report)
    return report


def worker(root, index, execute):
    folder = Path(root)/'shards'/str(index)
    command = [sys.executable, '-X', 'utf8', '-u', '-m',
               'ForecastAgent.research_loop.live_trial', '--parents', str(folder/'parents.json'),
               '--root', str(folder)]
    if execute:
        command.append('--execute')
    with (folder/'stdout.log').open('a', encoding='utf-8') as out, (folder/'stderr.log').open('a', encoding='utf-8') as err:
        process = subprocess.Popen(command, stdout=out, stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        save(folder/'worker.json', {'pid': process.pid, 'started_at_utc': now(),
            'command': command, 'credential_source': 'inherited process environment; values not recorded'})
        return process.wait()


def run(cohort_path, root, workers=3, execute=False):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        identity = freeze(load(cohort_path), root, workers)
        save(root/'worker.json', {'pid': os.getpid(), 'started_at_utc': now(),
            'execute': execute, 'submission_enabled': False})
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker, root, i, execute): i for i in range(workers)}
            exits = {}; last_count = -1
            while futures:
                ready, _ = concurrent.futures.wait(futures, timeout=10,
                    return_when=concurrent.futures.FIRST_COMPLETED)
                for future in ready:
                    index = futures.pop(future); exits[index] = future.result()
                report = aggregate(root, identity, finished=not futures, exits=exits, execute=execute)
                if report['processed_cases'] != last_count or not futures:
                    print(json.dumps({k: report[k] for k in
                        ('status', 'processed_cases', 'requested_cases', 'paired_completed', 'streams')}), flush=True)
                    last_count = report['processed_cases']
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--workers', default=3, type=int)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    run(args.cohort, args.root, args.workers, args.execute)


if __name__ == '__main__':
    main()
