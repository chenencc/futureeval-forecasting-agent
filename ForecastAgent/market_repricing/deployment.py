"""Independent immutable market deployment, without competition release changes."""
import argparse
import os
from pathlib import Path
import re
import subprocess

from . import ENGINE_SHA
from .common import digest, now, read, write

VERSION = 'market-v0.3.9-discovery.1'
BUDGET_PREDECESSOR = '0f3101af9b574194620baa54826654d122483361'
FROZEN_DAILY_LIMITS = {BUDGET_PREDECESSOR: 400,
                       'd00bc630300a22ee84d1f6064892f261894d833b': 900,
                       '7690c6a94ebfcada8f024de53f842ec44d74466d': 1500,
                       # Discovery-only successor retains this deployed capture,
                       # search, analysis and policy; pending tasks keep all quotas.
                       'f154a406a9611ebd4a64fc4077bb74778659b808': 1500}
PENDING = {'queued', 'running', 'retry_wait', 'budget_wait'}


def budget_compatibility():
    """Pin old stages; allow only a separately declared acquisition candidate."""
    import hashlib
    root = Path(__file__).parent
    manifest = read(root / 'budget_compatibility.json')
    if manifest['predecessor'] != BUDGET_PREDECESSOR:
        raise ValueError('Unexpected budget compatibility predecessor')
    candidate_path=root/'capture_stage_candidate.json'
    candidate=read(candidate_path) if candidate_path.exists() else None
    if candidate:
        if (candidate.get('parent_pipeline_commit')!='098a56c197012661f976d34f15cf21ac4106d024' or
                set(candidate.get('candidate_files_sha256_lf',{}))!={'evidence_capture.py','materials.py','acquisition_contract.py'}):
            raise ValueError('Undeclared capture-stage candidate')
        for name,expected in candidate['candidate_files_sha256_lf'].items():
            if hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=expected:
                raise ValueError('Capture candidate source changed: '+name)
        from ForecastAgent.acquisition_shared import identity as shared_identity
        if candidate.get('shared_acquisition')!=shared_identity():
            raise ValueError('Shared acquisition capability changed')
    for name, expected in manifest['unchanged_stage_files_sha256_lf'].items():
        if candidate and name in candidate['candidate_files_sha256_lf']:
            if candidate.get('legacy_stage_sha256_lf',{}).get(name)!=expected:
                raise ValueError('Capture candidate changed legacy baseline: '+name)
            continue
        if hashlib.sha256((root / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise ValueError('Budget promotion changed frozen stage: ' + name)
    return digest({'legacy_budget_manifest':manifest,'declared_capture_candidate':candidate}) if candidate else digest(manifest)


def task_release():
    """Offline fixtures may omit a pin; production checkout verification may not."""
    sha = os.environ.get('MARKET_PIPELINE_SHA')
    if not sha:
        return {}
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Market deployment must use an immutable full commit SHA')
    from .official_arm import ARMS, VERSION as analysis_version
    from .search_policy import identity as search_identity
    from .context_delivery import identity as context_identity
    return {'pipeline_version': VERSION, 'pipeline_commit': sha,
            'analysis_version': analysis_version, 'analysis_arms': list(ARMS),
            'market_search_profile': search_identity(), 'market_context_runtime': context_identity()}


def verify_checkout(root, expected):
    if not re.fullmatch('[0-9a-f]{40}', expected or ''):
        raise ValueError('MARKET_REPRICING_SHA must be an immutable full commit SHA')
    root = Path(root).resolve()
    sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if sha != expected:
        raise ValueError('Market implementation does not match MARKET_REPRICING_SHA')
    subprocess.run(['git', '-C', str(root), 'diff', '--quiet', 'HEAD'], check=True)
    if not Path(__file__).resolve().is_relative_to(root):
        raise ValueError('Market module is outside the selected deployment checkout')
    from .evidence_capture import policy_identity
    from .common import policy
    from .official_arm import implementation, ARMS
    from .search_policy import identity as search_identity
    from .context_delivery import identity as context_identity
    return {'schema': 'market-independent-deployment-v1', 'version': VERSION,
            'pipeline_commit': sha, 'engine_sha': ENGINE_SHA,
            'policy_sha256': digest(policy()), 'capture_identity': policy_identity(),
            'version_control': 'MARKET_REPRICING_SHA', 'budget_reset': False,
            'competition_release_changed': False, 'analysis_arms': list(ARMS),
            'official_map_identity': implementation(), 'market_search_profile': search_identity(),
            'context_runtime': context_identity()}


def validate_pending(worker_root, monitor_root, receipt):
    """Terminal cases are immutable; incompatible active stages block promotion."""
    from .evidence_capture import policy_identity
    worker_root, monitor_root = Path(worker_root), Path(monitor_root)
    worker = read(worker_root / 'state.json') if (worker_root / 'state.json').exists() else {'tasks': {}}
    monitor = read(monitor_root / 'state.json') if (monitor_root / 'state.json').exists() else {'tasks': {}}
    pending = {key: task for key, task in worker['tasks'].items() if task['status'] in PENDING}
    pending.update({key: task for key, task in monitor['tasks'].items() if key not in worker['tasks']})
    expected = policy_identity()
    for key, task in pending.items():
        old = task.get('pipeline_commit') in FROZEN_DAILY_LIMITS
        if old:
            budget_compatibility()
        if task.get('pipeline_commit') not in (None, receipt['pipeline_commit'], *FROZEN_DAILY_LIMITS):
            raise ValueError('Pending market task requires its frozen pipeline commit: ' + key)
        folder = worker_root / 'tasks' / digest(key)
        request_path = folder / 'collection-request.json'
        if request_path.exists():
            request = read(request_path)
            identity = request.get('market_capture_identity')
            if identity is not None and identity != expected:
                raise ValueError('Pending market capture requires its original implementation: ' + key)
            search_profile = request.get('market_search_profile')
            if search_profile is not None and search_profile != receipt.get('market_search_profile'):
                raise ValueError('Pending market search requires its original profile: ' + key)
        identity_path = folder / 'evidence-capture-v2' / 'identity.json'
        if identity_path.exists():
            saved = read(identity_path)
            if {k: v for k, v in saved.items() if k != 'parent_sha256'} != expected:
                raise ValueError('Pending market supplement requires its original implementation: ' + key)
    return {'pending_checked': len(pending), 'terminal_cases_preserved': sum(
        task['status'] not in PENDING for task in worker['tasks'].values()), 'budget_reset': False,
        'budget_compatibility_sha256': budget_compatibility()}


def record(root, receipt):
    """Record runtime provenance outside immutable task and provider ledgers."""
    root = Path(root)
    if not (root / 'state.json').exists():
        raise ValueError('A deployment record cannot bootstrap an empty task ledger')
    path = root / 'deployments' / (receipt['pipeline_commit'] + '.json')
    if path.exists():
        saved = read(path)
        if {k: v for k, v in saved.items() if k != 'first_seen_at'} != receipt:
            raise ValueError('Frozen market deployment receipt changed')
    else:
        write(path, receipt | {'first_seen_at': now()})
    write(root / 'deployment-runtime.json', receipt | {'observed_at': now()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkout', default='.')
    parser.add_argument('--worker-root')
    parser.add_argument('--monitor-root')
    parser.add_argument('--record-root')
    args = parser.parse_args()
    receipt = verify_checkout(args.checkout, os.environ.get('MARKET_PIPELINE_SHA'))
    if args.worker_root or args.monitor_root:
        if not args.worker_root or not args.monitor_root:
            parser.error('Both worker and monitor roots are required for compatibility validation')
        receipt['compatibility'] = validate_pending(args.worker_root, args.monitor_root, receipt)
    if args.record_root:
        record(args.record_root, {k: v for k, v in receipt.items() if k != 'compatibility'})
    print(__import__('json').dumps(receipt))


if __name__ == '__main__':
    main()
