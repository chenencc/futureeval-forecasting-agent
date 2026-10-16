"""Cloud-only real original-byte recovery gate with no provider calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    from checkpoint_recovery import RECOVERY
    from ForecastAgent.market_repricing.common import verify
    root = Path(args.root)
    state_bytes = (root/'state.json').read_bytes()
    if hashlib.sha256(state_bytes).hexdigest() != RECOVERY['source_state_sha256']:
        raise ValueError('Original task/admission/provider ledger bytes changed')
    receipt = json.loads((root/'checkpoint-recovery-38811619270.json').read_text())
    if receipt != RECOVERY:
        raise ValueError('Explicit observation-gap receipt missing')
    state = json.loads(state_bytes)
    report = {'schema':'real-market-recovery-acceptance-v1', 'files_verified':verify(root),
        'state_sha256':hashlib.sha256(state_bytes).hexdigest(),
        'original_state_bytes_unchanged':True, 'task_count':len(state.get('tasks',{})),
        'original_root_fields':sorted(state), 'gap_receipt':receipt,
        'actual_model_calls':0, 'actual_search_calls':0, 'quotas_reset':False,
        'scope':'Exact last-published task and provider state preserved; failed-run public discovery observations remain explicitly unavailable.'}
    output = Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__': main()
