"""Independent immutable market deployment, without competition release changes."""
import argparse
import os
from pathlib import Path
import re
import subprocess

from . import ENGINE_SHA
from .common import digest, now, read, write

VERSION = 'market-v0.3.0'
PENDING = {'queued', 'running', 'retry_wait'}


def task_release():
    """Offline fixtures may omit a pin; production checkout verification may not."""
    sha = os.environ.get('MARKET_PIPELINE_SHA')
    if not sha:
        return {}
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Market deployment must use an immutable full commit SHA')
    from .official_arm import ARMS, VERSION as analysis_version
    return {'pipeline_version': VERSION, 'pipeline_commit': sha,
            'analysis_version': analysis_version, 'analysis_arms': list(ARMS)}


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
    return {'schema': 'market-independent-deployment-v1', 'version': VERSION,
            'pipeline_commit': sha, 'engine_sha': ENGINE_SHA,
            'policy_sha256': digest(policy()), 'capture_identity': policy_identity(),
            'version_control': 'MARKET_REPRICING_SHA', 'budget_reset': False,
            'competition_release_changed': False, 'analysis_arms': list(ARMS),
            'official_map_identity': implementation()}


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
        if task.get('pipeline_commit') not in (None, receipt['pipeline_commit']):
            raise ValueError('Pending market task requires its frozen pipeline commit: ' + key)
        folder = worker_root / 'tasks' / digest(key)
        request_path = folder / 'collection-request.json'
        if request_path.exists():
            identity = read(request_path).get('market_capture_identity')
            if identity is not None and identity != expected:
                raise ValueError('Pending market capture requires its original implementation: ' + key)
        identity_path = folder / 'evidence-capture-v2' / 'identity.json'
        if identity_path.exists():
            saved = read(identity_path)
            if {k: v for k, v in saved.items() if k != 'parent_sha256'} != expected:
                raise ValueError('Pending market supplement requires its original implementation: ' + key)
    return {'pending_checked': len(pending), 'terminal_cases_preserved': sum(
        task['status'] not in PENDING for task in worker['tasks'].values()), 'budget_reset': False}


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
