"""Split resumable state from immutable changed evidence without resetting budgets."""
import argparse
import os
import hashlib
import json
import shutil
from pathlib import Path

TERMINAL = {'accepted', 'already_forecasted', 'closed', 'deadline_missed',
            'blocked_integrity', 'provider_blocked', 'platform_rejected'}


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def split(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or root in output.parents or output in root.parents:
        raise ValueError('State and output directories must be disjoint')
    if output.exists():
        raise ValueError('Use a fresh output directory')
    state = json.loads((root / 'campaign.json').read_text(encoding='utf-8'))
    if state.get('schema') != 'official-competition-v1' or state.get('tournament') != os.environ.get('FORECAST_TOURNAMENT', 'fall-futureeval-2026'):
        raise ValueError('Unexpected campaign identity')
    previous_path = root / 'archive-manifest.json'
    previous = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
    previous_files = previous.get('files', {})
    inventory = dict(previous_files)
    checkpoint, archive = output / 'checkpoint', output / 'evidence'
    checkpoint.mkdir(parents=True)
    archive.mkdir()
    changed = {}
    retained = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlinks are not valid state')
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if path.name in {'archive-manifest.json', 'checkpoint-inventory.json'} or path.name.endswith('.lock'):
            continue
        ident = parts[1] if len(parts) > 1 and parts[0] == 'tasks' else None
        if ident is not None and ident not in state['tasks']:
            raise ValueError('Task directory has no campaign ledger')
        info = {'sha256': sha(path), 'size_bytes': path.stat().st_size}
        key = relative.as_posix()
        inventory[key] = info
        if previous_files.get(key) != info:
            target = archive / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            changed[key] = info
        # Preserve every byte of nonterminal tasks, including search/provider ledgers.
        # Terminal evidence is archived; receipts and candidate identities remain online.
        keep = (ident is None and parts[0] not in {'cache', 'legacy'}) or (
            ident is not None and (state['tasks'][ident]['stage'] not in TERMINAL or
                len(parts) == 3 and path.name in {'candidate.json', 'submission.json', 'failure.json'}))
        # Legacy ledgers remain necessary until the first official adoption completes.
        if parts[0] == 'legacy':
            keep = True
        if keep:
            target = checkpoint / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            retained[key] = info
    manifest = {'schema': 'official-evidence-inventory-v1', 'files': inventory}
    (checkpoint / 'archive-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    retained['archive-manifest.json'] = {'sha256': sha(checkpoint / 'archive-manifest.json'),
        'size_bytes': (checkpoint / 'archive-manifest.json').stat().st_size}
    (checkpoint / 'checkpoint-inventory.json').write_text(json.dumps({
        'schema': 'official-checkpoint-v1', 'files': retained}, indent=2), encoding='utf-8')
    (archive / 'delta-manifest.json').write_text(json.dumps({
        'schema': 'official-evidence-delta-v1', 'run_id': __import__('os').environ.get('GITHUB_RUN_ID'),
        'previous_manifest_sha256': sha(previous_path) if previous_path.exists() else None,
        'files': changed}, indent=2), encoding='utf-8')
    report = {'checkpoint_bytes': sum(x['size_bytes'] for x in retained.values()),
        'changed_evidence_bytes': sum(x['size_bytes'] for x in changed.values()),
        'changed_files': len(changed), 'full_pending_tasks_preserved': True,
        'quota_reset': False, 'task_count': len(state['tasks'])}
    (output / 'storage-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def verify(root):
    root = Path(root)
    path = root / 'checkpoint-inventory.json'
    if not path.exists():
        if (root / 'campaign.json').exists():
            return {'legacy_full_state': True}
        raise ValueError('Recovery campaign is missing')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 'official-checkpoint-v1':
        raise ValueError('Unknown checkpoint schema')
    for name, expected in data['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe checkpoint path')
        file = root / relative
        if not file.is_file() or file.stat().st_size != expected['size_bytes'] or sha(file) != expected['sha256']:
            raise ValueError('Checkpoint hash mismatch: ' + name)
    if 'campaign.json' not in data['files']:
        raise ValueError('Campaign was not sealed in checkpoint')
    return {'verified_files': len(data['files']), 'quota_reset': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['split', 'verify'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    print(json.dumps(split(args.root, args.output) if args.action == 'split' else verify(args.root)))
