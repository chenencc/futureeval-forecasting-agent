"""Frozen paired acquisition experiment, with no search or model credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--parent',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--current',type=Path,required=True)
    parser.add_argument('--batch',type=int,choices=range(4),required=True)
    args=parser.parse_args()
    manifest=json.loads(Path(__file__).with_name('capture_acceptance_20.json').read_text())
    ids=manifest['task_ids'][args.batch*5:args.batch*5+5]
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    frozen={**manifest,'selected_batch':args.batch,'selected_ids':ids}
    (output/'frozen-manifest.json').write_text(json.dumps(frozen,indent=2))
    archive=output/'frozen-parent.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.write(args.parent/'campaign.json','campaign.json')
        for ident in ids:
            source=args.parent/'tasks'/ident/'bundle.json'
            if hashlib.sha256(source.read_bytes()).hexdigest()!=manifest['tasks'][ident]['bundle_sha256']:
                raise ValueError('Frozen bundle identity changed: '+ident)
            z.write(source,f'tasks/{ident}/bundle.json')
    journal=[]
    for ident in ids:
        for arm in manifest['tasks'][ident]['order']:
            code=(args.baseline if arm=='baseline' else args.current).resolve()
            env={k:v for k,v in os.environ.items() if not any(secret in k.upper() for secret in ['API_KEY','API_TOKEN','OPENROUTER','TAVILY','EXA_API','METACULUS_TOKEN','GH_TOKEN','GITHUB_TOKEN'])}
            env.update(PYTHONPATH=str(code),SUPPLEMENT_SOURCE_RUN_ID=str(manifest['source_run']))
            target=output/arm/ident
            program='from pathlib import Path;import sys;from ForecastAgent.supplement.stage import run;run(Path(sys.argv[1]),Path(sys.argv[2]),[sys.argv[3]],network=True,browser_limit=1,http_limit=2)'
            result=subprocess.run(['python','-c',program,str(archive),str(target),ident],cwd=code,env=env,timeout=180)
            journal.append({'task_id':ident,'arm':arm,'returncode':result.returncode})
            (output/'execution-journal.json').write_text(json.dumps(journal,indent=2))
    if any(row['returncode'] for row in journal):raise RuntimeError('Incomplete experiment; inspect preserved state')

if __name__=='__main__':main()
