"""Restore diagnosed legacy task bytes from their exact parent delta archive."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
from ForecastAgent.competition.recovery_policy import LEGACY_LOOKUP_ERROR
from ForecastAgent.competition.queue import load, save


def wanted(root):
    state=load(Path(root)/'campaign.json')
    return [ident for ident,t in state['tasks'].items() if t.get('stage')=='blocked_integrity'
        and t.get('last_error')==LEGACY_LOOKUP_ERROR and not (Path(root)/'tasks'/ident/'submission.json').exists()]


def restore(root,evidence):
    root,evidence=Path(root).resolve(),Path(evidence).resolve()
    inventory=load(root/'archive-manifest.json')['files']
    delta=load(evidence/'delta-manifest.json')['files']
    restored=[]
    for ident in wanted(root):
        prefix=f'tasks/{ident}/'
        names=[n for n in inventory if n.startswith(prefix)]
        for name in names:
            target=(root/name).resolve()
            if not target.is_relative_to(root):raise ValueError('Unapproved restore path')
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest()!=inventory[name]['sha256']:
                    raise ValueError('Existing restore file hash mismatch')
                continue
            source=(evidence/name).resolve()
            if not source.is_relative_to(evidence) or delta.get(name)!=inventory[name] or not source.is_file():
                raise ValueError('Exact parent evidence unavailable; preserve blocked task')
            if hashlib.sha256(source.read_bytes()).hexdigest()!=inventory[name]['sha256']:
                raise ValueError('Parent evidence hash mismatch')
        # Verify the entire task before copying any file.
        for name in names:
            target=root/name
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(evidence/name,target)
                restored.append(name)
    save(root/'legacy-recovery-restore.json',{'files':restored,'budget_reset':False})
    return restored


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--evidence',type=Path);a=p.parse_args()
    print(json.dumps({'wanted':wanted(a.root)} if a.evidence is None else {'restored':restore(a.root,a.evidence)}))
