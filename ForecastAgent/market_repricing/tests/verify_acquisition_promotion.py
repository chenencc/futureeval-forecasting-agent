"""Read-only production restoration gate; no model, search, or ledger writes."""
import argparse
import hashlib
from pathlib import Path
import subprocess
from collections import Counter
from ForecastAgent.market_repricing.common import read,write,now,verify
from ForecastAgent.market_repricing.deployment import verify_checkout,validate_pending


def check(root,output):
    root=Path(root)
    checked=verify(root)
    inventory=read(root/'inventory.json')['files']
    before={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in inventory}
    if before!=inventory:raise ValueError('Restored inventory does not match original hashes')
    saved=read(root/'state.json')
    sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    release=verify_checkout('.',sha)
    compatibility=validate_pending(root,root.parent/'no-monitor',release)
    after={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in inventory}
    if after!=before:raise ValueError('Promotion gate modified preserved originals')
    report={'schema':'market-acquisition-promotion-v1','checked_at':now(),'release':release,
        'compatibility':compatibility,'source_verification':checked,
        'original_file_count':len(before),'original_files_unchanged':True,
        'state_distribution':dict(Counter(t['status'] for t in saved['tasks'].values())),
        'original_task_count':len(saved['tasks']),
        'physical_provider_reservations_preserved':len(saved.get('provider_reservations',[])),
        'provider_calls':0,'budgets_reset':False,'terminal_tasks_reopened':False}
    write(output,report)
    print(__import__('json').dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();check(args.root,args.output)
