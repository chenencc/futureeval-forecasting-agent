"""Explicit fresh-budget raw recall experiment; prior ledgers stay immutable."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from ForecastAgent.collection_campaign import read, write, digest
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.task_lock import task_lock


def run(root, fixture, authorization, runner=run_retrieval):
    root,fixture=Path(root),Path(fixture)
    if len(authorization.strip()) < 20:
        raise ValueError('Record explicit fresh-budget authorization')
    if os.environ.get('GITHUB_RUN_ATTEMPT','1') != '1':
        raise ValueError('Workflow reruns cannot reset this experiment budget; preserve original state')
    rows=read(fixture)
    if len(rows)!=5 or len({str(r['id']) for r in rows})!=5:
        raise ValueError('Exactly five distinct frozen questions required')
    root.mkdir(parents=True,exist_ok=True)
    with task_lock(root):
        manifest=root/'experiment.json'
        if manifest.exists() or (root/'tasks').exists():
            raise ValueError('Experiment already exists; refusing silent budget reset or duplicate execution')
        experiment={'schema':'raw_recall_fresh_five_v1','created_at_utc':datetime.now(timezone.utc).isoformat(),
            'authorization':authorization,'baseline_run':'36837727870',
            'fixture_sha256':digest(rows),'question_ids':[str(r['id']) for r in rows],
            'code_commit':os.environ.get('GITHUB_SHA'),'run_id':os.environ.get('GITHUB_RUN_ID'),
            'budget_reset':True,'prior_ledgers_modified':False,'prior_captures_imported':False,
            'limits':{'tavily_basic_per_task':3,'exa_per_task':1,'shared_http_per_task':8,
                'extract_batches_per_task':1,'model_http_per_task_dispatch':16,'model_decisions_per_task':12},
            'tasks':{},'forecast_submissions':False,
            'comparison_caveat':'Same questions, independent fresh runs. Acquisition objective and retrieval randomness differ; not a controlled A/B or historical backtest.'}
        write(manifest,experiment)
        for row in rows:
            ident=str(row['id'])
            request={**row,'acquisition_focus':'raw_recall'}
            directory=root/'tasks'/ident;directory.mkdir(parents=True)
            with task_lock(directory):
                task=RetrievalTask(directory,request);task.save()
                if task.bundle['searches'] or task.bundle.get('model_attempts') or task.bundle['exa_searches']:
                    raise ValueError('Fresh experiment unexpectedly contains consumed budgets')
            entry={'status':'reserved','request_sha256':digest(request),'attempts':1}
            entry['budgets_before']={'tavily':len(task.bundle['searches']),
                'exa':len(task.bundle['exa_searches']),'model':len(task.bundle.get('model_attempts',[])),
                'source_http':len(task.bundle['fetch_attempts']), 'extract':len(task.bundle['extract_attempts'])}
            experiment['tasks'][ident]=entry;write(manifest,experiment)
            try:
                output=runner(request,directory,os.environ['TAVILY_API_KEY'],os.environ['OPENROUTER_API_KEY'])
                entry.update(status='interrupted' if (output.get('result') or {}).get('incomplete') else 'exported',
                    result=output.get('result'),acceptance=(output.get('acceptance') or {}).get('status'))
            except Exception as exc:
                entry.update(status='failed',error=type(exc).__name__)
            finally:
                entry['finished_at_utc']=datetime.now(timezone.utc).isoformat();write(manifest,experiment)
                print(json.dumps({'question_id':ident,'status':entry['status']},ensure_ascii=True),flush=True)
        return experiment


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--fixture',required=True,type=Path)
    parser.add_argument('--authorization',required=True)
    args=parser.parse_args()
    result=run(args.root,args.fixture,args.authorization)
    if any(row['status']=='failed' for row in result['tasks'].values()):
        raise SystemExit('One or more tasks failed; preserved experiment and task state contain the details')


if __name__=='__main__':main()
