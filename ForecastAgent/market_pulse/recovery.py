"""Explicit, audited recovery of an empty pre-search financial pilot.

Original files remain immutable. Native model journals, counters and sessions
are copied; only the financial policy identity and resumable result migrate.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil

from ForecastAgent.competition.queue import load, save
from ForecastAgent.market_pulse.collection import prepare as financial_prepare, policy_hashes
from ForecastAgent.market_pulse import pilot
from ForecastAgent.acquisition import pipeline
from ForecastAgent.releases.v1_0_5 import verify_release, native_manifest


def without_policy(request):
    return {k: v for k, v in request.items() if k != 'financial_acquisition_policy'}


def eligible(bundle, collection):
    """This migration handles empty startup failures, never acquired tasks."""
    if any(bundle.get(key) for key in ('searches', 'exa_searches', 'fetch_attempts',
            'extract_attempts', 'pages', 'market_snapshots', 'update_attempts')):
        raise ValueError('Recovery requires no acquisition resource consumption')
    result = bundle.get('result') or {}
    if result.get('termination_reason') not in {'stalled', 'context_projection_failure', 'plan_repair_limit'}:
        raise ValueError('Recovery requires the audited startup failure')
    if not 0 < len(bundle.get('sessions', [])) < 3:
        raise ValueError('Recovery must preserve the three-execution lifetime cap')
    attempts = bundle.get('model_attempts', [])
    if not 0 < len(attempts) < 72 or any(a.get('status') != 'received' for a in attempts):
        raise ValueError('Recovery requires completed model receipts within lifetime cap')
    for attempt in attempts:
        path = (collection / attempt['path']).resolve()
        if not path.is_relative_to(collection.resolve()) or pilot.sha(path) != attempt['sha256']:
            raise ValueError('Original model journal changed')
    expected = hashlib.sha256(json.dumps(bundle['request'], sort_keys=True).encode()).hexdigest()
    if bundle['request_hash'] != expected:
        raise ValueError('Original request identity changed')


def release_request(request):
    result = copy.deepcopy(request)
    result.update(acquisition_strategy='intelligent_materials_v3',
        drain_unseen_reads_before_stall=True, source_reading_policy='crawl4ai_v1',
        recover_sources_before_stall=True,
        source_recovery_policy_sha256=hashlib.sha256((pilot.WORKSPACE /
            'ForecastAgent/runtime/source_frontier.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        exa_search_policy='required')
    return result


def prepare(source, root):
    source, root = Path(source).resolve(), Path(root).resolve()
    if root.exists() or root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError('Recovery needs a new disjoint directory')
    verify_release()
    status = load(source / 'run-status.json')
    manifest = load(source / 'manifest.json')
    if status['state'] != 'finished' or manifest['model'] != pilot.MODEL:
        raise ValueError('Original pilot must be finished with the fixed model')
    if os.environ.get('FORECAST_MODEL') != pilot.MODEL or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '0':
        raise ValueError('Recovery must keep the original model routing')
    if manifest['runner_sha256'] != pilot.sha(source / 'executed-runner-source.py'):
        raise ValueError('Original executed runner changed')
    if manifest['release_manifest_sha256'] != pilot.sha(pilot.WORKSPACE / 'ForecastAgent/releases/manifest.json'):
        raise ValueError('Frozen release changed')
    originals = native_manifest(source)
    prepared = []
    # Validate all five inputs and receipts before creating any target files.
    for row in manifest['rows']:
        if pilot.sha(source / row['input']) != row['input_sha256']:
            raise ValueError('Frozen input changed')
        native = source / 'tasks' / row['id'] / 'retrieval/release-1.0.5'
        bundle = load(native / 'collection/bundle.json')
        eligible(bundle, native / 'collection')
        old_identity = load(native / 'identity.json')
        if old_identity['request'] != bundle['request']:
            raise ValueError('Original native identity changed')
        request = financial_prepare(without_policy(load(source / row['input'])))
        frozen = pipeline.identity(release_request(request), True)
        if without_policy(frozen['request']) != without_policy(bundle['request']):
            raise ValueError('Recovery cannot change question or acquisition budgets')
        fixed = copy.deepcopy(bundle)
        fixed.update(request=frozen['request'], request_hash=hashlib.sha256(
            json.dumps(frozen['request'], sort_keys=True).encode()).hexdigest(),
            result={'status': 'partial', 'incomplete': True, 'resumable': True,
                    'termination_reason': 'financial_context_recovery'})
        fixed['financial_recovery'] = {'source_bundle_sha256': pilot.sha(native / 'collection/bundle.json'),
            'original_result': copy.deepcopy(bundle['result']), 'created_at_utc': pilot.now(),
            'budget_reset': False, 'preserved_model_attempts': len(bundle['model_attempts'])}
        for key in bundle:
            if key not in {'request', 'request_hash', 'result'} and fixed[key] != bundle[key]:
                raise ValueError('Recovery modified the prior ledger')
        prepared.append((row, native, request, frozen, fixed))
    root.mkdir(parents=True)
    shutil.copytree(source / 'official', root / 'official')
    rows, receipts = [], []
    for row, native, request, frozen, fixed in prepared:
        destination = root / 'tasks' / row['id'] / 'retrieval/release-1.0.5'
        shutil.copytree(native / 'collection', destination / 'collection')
        save(destination / 'identity.json', frozen)
        save(destination / 'state.json', {'stage': 'collection', 'identity_sha256': pipeline.digest(frozen)})
        save(destination / 'collection/bundle.json', fixed)
        save(root / row['input'], request)
        rows.append({**row, 'input_sha256': pilot.sha(root / row['input'])})
        receipts.append({'id': row['id'], 'model_attempts_preserved': len(fixed['model_attempts']),
            'sessions_preserved': len(fixed['sessions']), 'budgets_preserved': fixed['acquisition_limits'],
            'original_collection_files_sha256': native_manifest(native / 'collection')})
    manifest = {**manifest, 'created_at_utc': pilot.now(), 'rows': rows,
        'runner_sha256': pilot.sha(pilot.__file__), 'financial_policy_source_sha256': policy_hashes(),
        'new_pilot': False, 'recovery_from': str(source), 'budget_reset': False}
    (root / 'executed-runner-source.py').write_bytes(Path(pilot.__file__).read_bytes())
    save(root / 'manifest.json', manifest)
    save(root / 'recovery-receipt.json', {'source': str(source), 'source_files_sha256': originals,
        'tasks': receipts, 'analysis_run': False, 'submitted': False, 'budget_reset': False})
    if native_manifest(source) != originals:
        raise ValueError('Source pilot changed during recovery preparation')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.source, args.root)
    print(json.dumps({'prepared': len(result['rows']), 'budget_reset': False}))
