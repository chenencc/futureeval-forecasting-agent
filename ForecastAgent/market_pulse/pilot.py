"""Bounded local v105 acquisition pilot; no analysis or platform submission."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import re
import subprocess
import sys

# Install before release modules capture urllib.request.urlopen by name.
# Installing inside child() is too late for OpenRouter and basic Search aliases.
if os.environ.get('FORECAST_PROVIDER_FAILOVER_MODULE'):
    runpy.run_path(os.environ['FORECAST_PROVIDER_FAILOVER_MODULE'])['install']()

from ForecastAgent.competition.queue import load, save
from ForecastAgent.market_pulse.inventory import snapshot, SLUG
from ForecastAgent.runtime.task_lock import task_lock

WORKSPACE = Path(__file__).resolve().parents[2]
MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'
SELECTED = ('46190', '46197', '46191')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def selected_ids(ids=None):
    values = tuple(SELECTED if ids is None else ids)
    if (not 1 <= len(values) <= 5 or len(set(values)) != len(values) or
            any(not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,20}', value) for value in values)):
        raise ValueError('Select one to five distinct numeric question IDs')
    return values


def prepare(root, *, adopt_startup_fix=False, ids=None):
    from ForecastAgent.releases.v1_0_5 import verify_release
    from ForecastAgent.market_pulse.collection import policy_hashes, prepare as financial_prepare
    verify_release()
    manifest_path = root / 'manifest.json'
    requested_ids = selected_ids(ids)
    if manifest_path.exists():
        manifest = load(manifest_path)
        if ids is not None and tuple(row['id'] for row in manifest['rows']) != requested_ids:
            raise ValueError('Frozen pilot question selection changed')
        if (manifest.get('financial_policy_source_sha256') is not None and
                manifest['financial_policy_source_sha256'] != policy_hashes()):
            raise ValueError('Frozen financial acquisition policy changed')
        if manifest['release_manifest_sha256'] != sha(WORKSPACE / 'ForecastAgent/releases/manifest.json'):
            raise ValueError('Frozen release changed')
        for row in manifest['rows']:
            if sha(root / row['input']) != row['input_sha256']:
                raise ValueError('Frozen pilot input changed')
        if manifest['runner_sha256'] != sha(__file__):
            if not adopt_startup_fix:
                raise ValueError('Frozen pilot runner changed')
            for row in manifest['rows']:
                folder = root / 'tasks' / row['id']
                failure = load(folder / 'collection-result.json')
                if failure.get('status') != 'failed' or failure.get('error_type') != 'ValueError':
                    raise ValueError('Only the audited startup failure may be adopted')
                if any(folder.rglob('bundle.json')) or any(folder.rglob('identity.json')):
                    raise ValueError('An acquisition ledger already exists; refuse startup migration')
                save(folder / 'startup-failure-preserved.json', failure)
            save(root / 'startup-manifest-preserved.json', manifest)
            manifest.update(runner_sha256=sha(__file__), startup_fix_adopted_at_utc=now(),
                startup_fix='Separate collector ledger from runner logs; inputs and budgets unchanged.')
            save(manifest_path, manifest)
        return manifest
    if (root / 'official').exists():
        raise ValueError('Partial pilot preparation exists; inspect rather than silently recapture')
    report = snapshot(root / 'official')
    selected = {r['question_id']: r for r in report['rows'] if r['question_id'] in requested_ids}
    if set(selected) != set(requested_ids) or any(not r['automatic_candidate'] for r in selected.values()):
        raise ValueError('Selected live questions are missing or require review')
    rows = []
    for ident in requested_ids:
        packet = load(root / 'official/inputs' / f'{ident}.json')
        relative = f'inputs/{ident}.json'
        save(root / relative, financial_prepare(packet['request']))
        rows.append({'id': ident, 'post_id': selected[ident]['post_id'],
                     'title': selected[ident]['title'], 'input': relative,
                     'input_sha256': sha(root / relative),
                     'conservative_deadline_utc': selected[ident]['conservative_deadline_utc']})
    transport = os.environ.get('FORECAST_PROVIDER_FAILOVER_MODULE')
    manifest = {'schema': 'market-pulse-v105-acquisition-pilot-v1', 'tournament': SLUG,
                'created_at_utc': now(), 'model': MODEL, 'workers': 2,
                'runner_sha256': sha(__file__), 'release': '1.0.5',
                'release_manifest_sha256': sha(WORKSPACE / 'ForecastAgent/releases/manifest.json'),
                'transport_module_sha256': sha(transport) if transport else None,
                'limits': {'tavily_basic_per_task': 3, 'exa_per_task': 1},
                'existing_campaign_budget_reset': False, 'new_pilot': True,
                'financial_customization': 'Versioned issuer routing, body checks and available-information objectives over frozen release.',
                'financial_policy_source_sha256': policy_hashes(),
                'analysis_run': False, 'submitted': False, 'rows': rows}
    (root / 'executed-runner-source.py').write_bytes(Path(__file__).read_bytes())
    save(manifest_path, manifest)
    return manifest


def child(source, folder):
    """Only release acquisition is reachable; no token reaches the collector."""
    if os.environ.get('METACULUS_TOKEN'):
        raise ValueError('Platform credential must not reach acquisition child')
    from ForecastAgent.market_pulse.collection import collect
    from ForecastAgent.acquisition.pipeline import resource_report
    folder.mkdir(parents=True, exist_ok=True)
    try:
        # Runner logs must not look like a prior collector ledger to the release.
        result = collect(load(source), folder / 'retrieval')
        native = folder / 'retrieval/release-1.0.5'
        raw = load(native / 'collection/bundle.json')
        stage = load(native / 'state.json')
        marker = result.get('release_acquisition') or {}
        package = load(native / marker['package_file']) if marker.get('package_file') else {}
        pages = package.get('pages', {})
        readable = [u for u, p in pages.items() if p.get('content') and p.get('body_diagnostics', {}).get('usable_text')]
        status = 'exported' if marker else 'incomplete'
        if result.get('result', {}).get('status') == 'material_unavailable':
            status = 'material_unavailable'
        report = {'status': status, 'finished_at_utc': now(), 'pipeline_stage': stage['stage'],
                  'readable_pages': len(readable), 'readable_urls': readable,
                  'original_collector_result': raw.get('result'),
                  'resources': resource_report(raw, native / 'collection', native / 'supplement'),
                  'gaps': package.get('gaps', []),
                  'package_path': marker.get('package_file'),
                  'package_sha256': marker.get('package_sha256'),
                  'semantic_complete': False, 'analysis_run': False, 'submitted': False,
                  'budget_reset': False}
        save(folder / 'collection-result.json', report)
    except Exception as exc:
        # Exception text may contain external response data; retain its class.
        save(folder / 'collection-result.json', {'status': 'failed', 'finished_at_utc': now(),
                'error_type': type(exc).__name__, 'state_preserved': True,
                'analysis_run': False, 'submitted': False, 'budget_reset': False})
        raise


def case(root, row, *, retry_startup=False):
    folder = root / 'tasks' / row['id']
    marker = folder / 'collection-result.json'
    # This invocation never silently retries a completed or failed task.
    if marker.exists() and not retry_startup:
        return load(marker)
    if retry_startup and ((folder / 'retrieval').exists() or not (folder / 'startup-failure-preserved.json').exists()):
        raise ValueError('Startup retry requires preserved failure and no acquisition ledger')
    folder.mkdir(parents=True, exist_ok=True)
    save(folder / 'started.json', {'started_at_utc': now(), 'budget_reset': False})
    env = dict(os.environ, PYTHONPATH=str(WORKSPACE), FORECAST_MODEL=MODEL,
               FORECAST_MODEL_FALLBACK_SUPER='0', PYTHONUTF8='1')
    env.pop('METACULUS_TOKEN', None)
    with (folder / 'stdout.log').open('a', encoding='utf-8') as out, (folder / 'stderr.log').open('a', encoding='utf-8') as err:
        result = subprocess.run([sys.executable, '-u', '-m', 'ForecastAgent.market_pulse.pilot',
                                 'case', '--input', str(root / row['input']), '--root', str(folder)],
                                cwd=WORKSPACE, env=env, stdout=out, stderr=err)
    if not marker.exists():
        save(marker, {'status': 'process_failed', 'returncode': result.returncode,
                      'state_preserved': True, 'analysis_run': False, 'submitted': False})
    return load(marker)


def progress(root, manifest, state):
    rows = []
    for row in manifest['rows']:
        folder = root / 'tasks' / row['id']
        marker = folder / 'collection-result.json'
        result = load(marker) if marker.exists() else {'status': 'running' if (folder / 'started.json').exists() else 'pending'}
        rows.append({**row, **result})
    report = {'schema': 'market-pulse-pilot-progress-v1', 'checked_at_utc': now(),
              'state': state, 'pid': os.getpid(), 'model': MODEL,
              'states': dict(Counter(row['status'] for row in rows)), 'rows': rows,
              'analysis_run': False, 'submitted': False, 'budget_reset': False}
    save(root / 'run-status.json', report)
    return report


def run(root, *, adopt_startup_fix=False, ids=None):
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        manifest = prepare(root, adopt_startup_fix=adopt_startup_fix, ids=ids)
        progress(root, manifest, 'running')
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(case, root, row, retry_startup=adopt_startup_fix) for row in manifest['rows']]
            pending = set(jobs)
            while pending:
                done, pending = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
                for job in done:
                    job.result()
                progress(root, manifest, 'running')
        report = progress(root, manifest, 'finished')
        print(json.dumps({'state': report['state'], 'states': report['states']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'case'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--adopt-startup-fix', action='store_true')
    parser.add_argument('--ids', help='One to five comma-separated IDs; frozen on first execution')
    args = parser.parse_args()
    if args.command == 'case':
        child(args.input, args.root)
    else:
        run(args.root, adopt_startup_fix=args.adopt_startup_fix,
            ids=args.ids.split(',') if args.ids is not None else None)
