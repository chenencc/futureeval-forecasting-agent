"""Refresh local vintage metrics after price synchronization, without networking."""
import argparse
import hashlib
from pathlib import Path
import sqlite3

from .common import digest, now, read, write
from .material_revisions import evaluate


def refresh(root, index_path=None, output=None):
    root = Path(root)
    index_path = Path(index_path) if index_path else root / 'prediction_markets/latest-material-revisions.json'
    output = Path(output) if output else root / 'reports/market-latest-vintage-price-metrics.json'
    if not index_path.exists():
        return {'status': 'no_material_vintages', 'network_requests': 0}
    index = read(index_path)
    db = sqlite3.connect('file:' + (root / 'index.sqlite3').as_posix() + '?mode=ro', uri=True)
    try:
        source = db.execute("SELECT f.sha256,a.run_id,a.artifact_id FROM files f JOIN artifacts a ON f.repo=a.repo AND f.artifact_id=a.artifact_id WHERE f.path='tracking.json' AND a.name='market-repricing-v2-monitor-state' AND a.status IN ('imported','files_imported') ORDER BY CAST(a.run_id AS INTEGER) DESC LIMIT 1").fetchone()
    finally:
        db.close()
    tracking = {'tasks': {}}
    receipt = {'status': 'no_archived_tracking'}
    if source:
        sha, run_id, artifact_id = source
        data = (root / 'blobs/sha256' / sha[:2] / sha).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError('Local price tracking bytes changed')
        tracking = __import__('json').loads(data)
        receipt = {'status': 'sha256_verified', 'run_id': run_id, 'artifact_id': artifact_id,
            'tracking_sha256': sha, 'observed_at': tracking.get('observed_at')}
    result = evaluate(index, tracking)
    result.update(generated_at=now(), price_source=receipt, index_sha256=digest(index),
        status='waiting_for_labels' if not any(r['arms'] for r in result['rows']) else 'scored',
        network_requests=0, new_provider_calls=0, revisions_not_modified=True)
    write(output, result)
    return {'status': result['status'], 'output': str(output), 'cases': len(index['cases']),
        'eligible_horizon_rows': sum(bool(r['arms']) for r in result['rows']), 'network_requests': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='E:/metaculus_data')
    args = parser.parse_args()
    print(__import__('json').dumps(refresh(args.root)))


if __name__ == '__main__':
    main()
