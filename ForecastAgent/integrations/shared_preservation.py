"""Read-only saved official campaign gate for a capability-only release upgrade."""
import argparse,json,hashlib
from pathlib import Path
from collections import Counter
from ForecastAgent.competition.live import TERMINAL
from ForecastAgent.releases.manifest import verify
from ForecastAgent.acquisition_shared import identity


def check(root,output):
    root=Path(root);manifest=verify(expected_version='1.0.5')
    if manifest.get('shared_acquisition')!=identity():raise ValueError('Competition shared capability mismatch')
    inventory=json.loads((root/'checkpoint-inventory.json').read_text())['files']
    for name,row in inventory.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError('Invalid official checkpoint path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Changed official checkpoint original')
    before={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in inventory}
    campaign=json.loads((root/'campaign.json').read_text())
    pending=[key for key,t in campaign['tasks'].items() if t['stage'] not in TERMINAL]
    if pending:raise ValueError('Unfinished competition tasks retain their original acquisition release: '+','.join(pending))
    submissions=[name for name in inventory if name.endswith('/submission.json')]
    receipts=[json.loads((root/name).read_text()) for name in submissions]
    assert before=={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in inventory}
    report={'schema':'competition-shared-acquisition-preservation-v1','checkpoint_files_verified':len(inventory),
      'task_count':len(campaign['tasks']),'state_distribution':dict(Counter(t['stage'] for t in campaign['tasks'].values())),
      'submission_receipt_count':len(receipts),'original_files_unchanged':True,
      'pending_tasks':pending,'shared_acquisition':identity(),'budgets_reset':False,'model_or_search_calls':0,
      'forecasts_submitted':False,'terminal_tasks_reopened':False}
    destination=Path(output);destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();check(a.root,a.output)
