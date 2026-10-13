"""Dispatch a serialized next batch only after publishing the current checkpoint."""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from .common import now, instant, policy, read, write
from .github_state import api
from .worker import pending_keys


def run(root, monitor_run, verification_only=False):
    root = Path(root)
    ledger, cfg = read(root / 'state.json'), policy()
    pending = [k for k,t in ledger['tasks'].items() if t['status'] in {'queued','running','retry_wait'}
               and t['executions'] < cfg['maximum_executions_per_task']]
    due = pending_keys(ledger)
    current = os.environ['GITHUB_RUN_ID']
    day = now()[:10]
    records = ledger.get('provider_reservations', [])
    used = sum(r['kind'] == 'model' and r['day'] == day for r in records)
    report = {'pending': len(pending), 'source_monitor_run': str(monitor_run),
              'budget_reset': False, 'status': 'idle'}
    if verification_only or not pending or used >= cfg['maximum_worker_model_attempts_per_utc_day']:
        return report
    runs = api('/actions/workflows/market_research_worker.yaml/runs?branch=main&per_page=30')['workflow_runs']
    if any(str(r['id']) != current and r['status'] in {'queued', 'in_progress', 'waiting', 'requested', 'pending'} for r in runs):
        return report | {'status': 'another_worker_already_scheduled'}
    payload = {'ref': 'main', 'inputs': {'monitor_run_id': str(monitor_run), 'verification_only': 'false'}}
    if not due:
        wake = min((ledger['tasks'][k]['retry_at'] for k in pending), key=instant)
        # The queued worker waits at most fifteen minutes per dispatch. This
        # closes a recorded retry deadline even when native schedules are late.
        payload['inputs']['not_before'] = wake
        report['retry_not_before'] = wake
    request = Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] +
        '/actions/workflows/market_research_worker.yaml/dispatches',
        data=json.dumps(payload).encode(), method='POST', headers={
            'Authorization': 'Bearer ' + os.environ['GH_STATE_TOKEN'],
            'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'})
    with urlopen(request, timeout=60) as response:
        if response.status != 204: raise RuntimeError('Next batch dispatch was not acknowledged')
    return report | {'status': 'next_batch_dispatch_acknowledged', 'http_status': 204}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--monitor-run', required=True)
    parser.add_argument('--verification-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.monitor_run, args.verification_only)))
