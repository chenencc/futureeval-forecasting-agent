"""Delete only GitHub artifacts with a verified immutable local ZIP backup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess


def api(gh, path, method='GET'):
    result = subprocess.run([gh, 'api', path, '--method', method], capture_output=True)
    if result.returncode:
        raise RuntimeError('GitHub API request failed; no unverified deletion is allowed')
    return json.loads(result.stdout or b'{}')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def run(root, repo, gh, *, execute=False):
    root = Path(root).resolve()
    connection = sqlite3.connect('file:' + (root / 'index.sqlite3').as_posix() + '?mode=ro', uri=True)
    try:
        rows = connection.execute('SELECT artifact_id,archive_sha256 FROM artifacts WHERE repo=? AND status="imported"', (repo,)).fetchall()
    finally:
        connection.close()
    imported = dict(rows)
    artifacts = []
    for page in range(1, 101):
        items = api(gh, f'repos/{repo}/actions/artifacts?per_page=100&page={page}')['artifacts']
        artifacts.extend(items)
        if len(items) < 100:
            break
    else:
        raise RuntimeError('Incomplete artifact inventory')
    # Production recovery state remains online. Finished research campaign
    # state is recoverable from its verified local ZIP, not retained indefinitely.
    groups = {}
    for item in artifacts:
        if not item['expired']:
            groups.setdefault(item['name'], []).append(item)
    recovery_names = {'futureeval-official-state', 'futureeval-official-control',
        'futureeval-monitor-state', 'futureeval-monitor-health', 'futureeval-collection-state'}
    protected = {a['id'] for name, group in groups.items() if name in recovery_names
        for a in sorted(group, key=lambda x: x['id'], reverse=True)[:3]}
    # Recent monitor scans can still be referenced by a delayed workflow_run event.
    scans = [a for a in artifacts if a['name'].startswith('futureeval-questions-') and not a['expired']]
    protected.update(a['id'] for a in sorted(scans, key=lambda x: x['id'], reverse=True)[:3])
    # Do not remove inputs/state used by active workers, even with a local backup.
    active = []
    for status in ('in_progress', 'queued', 'waiting', 'pending', 'requested'):
        active += api(gh, f'repos/{repo}/actions/runs?status={status}&per_page=100')['workflow_runs']
    if active and execute:
        raise RuntimeError('Active runs exist; retry cleanup when workers are idle')
    candidates = []
    for item in artifacts:
        ident = str(item['id'])
        expected = imported.get(ident)
        if item['expired'] or item['id'] in protected or not expected:
            continue
        archive = root / 'archives' / expected[:2] / (expected + '.zip')
        if not archive.is_file() or digest(archive) != expected:
            raise ValueError('Local archive missing or corrupt; retain remote artifact')
        candidates.append(item)
    report = {'schema': 'verified-artifact-prune-v1', 'repo': repo, 'execute': execute,
        'inventory_bytes': sum(a['size_in_bytes'] for a in artifacts if not a['expired']),
        'verified_candidate_bytes': sum(a['size_in_bytes'] for a in candidates),
        'candidate_ids': [a['id'] for a in candidates], 'protected_ids': sorted(protected),
        'active_runs': len(active), 'deleted_ids': []}
    for item in candidates if execute else []:
        api(gh, f"repos/{repo}/actions/artifacts/{item['id']}", 'DELETE')
        report['deleted_ids'].append(item['id'])
        # Persist each receipt before the next reversible remote deletion.
        receipt = root / 'reports' / 'artifact-prune-latest.json'
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='E:/metaculus_data')
    parser.add_argument('--repo', default='chenencc/futureeval-forecasting-agent')
    parser.add_argument('--gh-path', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.repo, args.gh_path, execute=args.execute)))
