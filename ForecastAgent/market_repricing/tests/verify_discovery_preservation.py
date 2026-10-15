"""Read-only real-monitor checkpoint replay; no external/provider requests."""
import argparse
from pathlib import Path

from ForecastAgent.market_repricing.common import digest, now, policy, read, verify, write
from ForecastAgent.market_repricing.discovery import scan, VERSION


def check(root, output):
    root = Path(root)
    verified = verify(root)
    before_bytes = (root / 'state.json').read_bytes()
    ledger = read(root / 'state.json')
    # Work on a private copy; the restored checkpoint is never written back.
    original_native_hash = digest({k: v for k, v in ledger.items() if k != 'discovery_windows'})
    tasks = digest(ledger['tasks'])
    admission_count = len(ledger['tasks'])
    cfg, clock = policy(), now()
    calls = []
    class EmptyHTTP:
        def get(self, url, params):
            calls.append({'url': url, 'params': params})
            return {'markets': []}, {'received_at': clock, 'response_sha256': digest([]), 'receipt': 'offline'}
    for venue in ('polymarket', 'kalshi'):
        saved = ledger.setdefault('discovery_windows', {}).setdefault(venue, {})
        audit = scan(EmptyHTTP(), venue, cfg, clock, saved, lambda rows, at: None)
        if audit['errors']:
            raise ValueError('Saved discovery cursor cannot be replayed offline')
    assert digest(ledger['tasks']) == tasks
    # The only allowed in-memory mutation is the new discovery journal.
    assert digest({k: v for k, v in ledger.items() if k != 'discovery_windows'}) == original_native_hash
    assert (root / 'state.json').read_bytes() == before_bytes
    assert verify(root) == verified
    report = {'schema': 'market-discovery-preservation-v1', 'checked_at': now(),
              'discovery_version': VERSION, 'original_files_verified': verified,
              'original_state_sha256': __import__('hashlib').sha256(before_bytes).hexdigest(),
              'native_tasks_preserved': admission_count, 'original_files_unchanged': True,
              'synthetic_public_calls': len(calls), 'actual_public_calls': 0,
              'model_search_calls': 0, 'quotas_reset': False, 'tasks_reopened': False}
    write(output, report)
    print(__import__('json').dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    check(args.root, args.output)
