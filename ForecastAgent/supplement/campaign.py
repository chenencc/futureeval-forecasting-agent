"""Assess a frozen acquisition queue in batches of five, preserving repair caps."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.supplement.stage import run, save, now


def members(archive):
    with zipfile.ZipFile(archive) as source:
        campaign=json.loads(source.read('campaign.json'))
        ids=list(campaign['tasks'])
        hashes={name:hashlib.sha256(source.read(name)).hexdigest()
            for name in ['campaign.json']+[f'tasks/{ident}/bundle.json' for ident in ids]}
    return ids,hashes


def run_campaign(archive,output,*,seed=None,network=True):
    archive=Path(archive);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with task_lock(output):
        ids,hashes=members(archive)
        if not ids or not all(ident.isdecimal() for ident in ids):raise ValueError('Invalid frozen task IDs')
        identity={'schema':'supplement_campaign_v1','parent_files_sha256':hashes,
            'source_run_id':os.environ.get('SUPPLEMENT_SOURCE_RUN_ID'),
            'task_ids':ids,'network':network,'batch_size':5,
            'limits_per_task':{'browser':2,'http':2,'reparse':8},
            'seed_run_id':os.environ.get('SUPPLEMENT_SEED_RUN_ID')}
        manifest=output/'campaign-manifest.json'
        if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8'))!=identity:
            raise ValueError('Frozen repair campaign changed')
        save(manifest,identity)
        state_path=output/'campaign-state.json'
        state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
            'schema':'supplement_campaign_state_v1','created_at_utc':now(),
            'tasks':{ident:{'status':'pending'} for ident in ids},'batches':[],
            'model_calls':0,'tavily_calls':0,'exa_calls':0,'forecast_submissions':False}
        seed_ids=[]
        if seed:
            seed=Path(seed)
            previous=json.loads((seed/'manifest.json').read_text(encoding='utf-8'))
            seed_ids=previous['ids']
            if not set(seed_ids)<=set(ids) or not 1<=len(seed_ids)<=5:raise ValueError('Invalid seed task selection')
            if previous.get('source_run_id')!=identity['source_run_id'] or previous.get('network_enabled')!=network:
                raise ValueError('Seed parent or execution policy changed')
            expected={name:hashes[name] for name in ['campaign.json']+[f'tasks/{ident}/bundle.json' for ident in seed_ids]}
            if previous.get('parent_files_sha256')!=expected:raise ValueError('Seed parent bytes changed')
            target=output/'batches'/'seed'
            if not target.exists():
                shutil.copytree(seed,target)
        batches=[('seed',seed_ids)] if seed_ids else []
        remaining=[ident for ident in ids if ident not in seed_ids]
        batches += [(f'batch-{index//5+1:03d}',remaining[index:index+5]) for index in range(0,len(remaining),5)]
        if not state['batches']:
            state['batches']=[{'name':name,'ids':selected,'status':'pending'} for name,selected in batches]
        elif [(b['name'],b['ids']) for b in state['batches']]!=batches:
            raise ValueError('Frozen batch partition changed')
        save(state_path,state)
        for batch in state['batches']:
            if batch['status']=='completed':continue
            batch.update(status='running',started_at_utc=now());save(state_path,state)
            try:
                summary=run(archive,output/'batches'/batch['name'],batch['ids'],network=network)
                for row in summary:
                    child_path=output/'batches'/batch['name']/'tasks'/row['task_id']/'supplement.json'
                    child=json.loads(child_path.read_text(encoding='utf-8'))
                    state['tasks'][row['task_id']]={'status':'assessed','batch':batch['name'],
                        **row,'attempts_by_method':{method:sum(a['method']==method for a in child['attempts']) for method in ['browser','http','reparse']},
                        'uncertain_reserved_attempts':sum(a['status']=='reserved' for a in child['attempts']),
                        'original_task_state':child['original_task_state']}
                batch.update(status='completed',finished_at_utc=now())
                save(state_path,state)
                print(json.dumps({'batch':batch['name'],'completed_tasks':sum(t['status']=='assessed' for t in state['tasks'].values()),'total_tasks':len(ids)}),flush=True)
            except Exception as exc:
                batch.update(status='interrupted',error=type(exc).__name__,detail=str(exc)[:500]);save(state_path,state)
                raise
        state['completed_at_utc']=now();state['all_tasks_assessed']=True;save(state_path,state)
        totals={method:sum(t['attempts_by_method'][method] for t in state['tasks'].values()) for method in ['browser','http','reparse']}
        save(output/'campaign-summary.json',{'schema':'supplement_campaign_summary_v1',
            'tasks_total':len(ids),'tasks_assessed':len(ids),
            'tasks_with_new_readable_captures':sum(t['new_readable_captures']>0 for t in state['tasks'].values()),
            'new_readable_captures':sum(t['new_readable_captures'] for t in state['tasks'].values()),
            'remaining_failed_source_gaps':sum(t['remaining_failed_source_gaps'] for t in state['tasks'].values()),
            'attempts_by_method':totals,'includes_preserved_seed_attempts':bool(seed_ids),
            'model_calls':0,'tavily_calls':0,'exa_calls':0,'forecast_submissions':False,
            'original_collection_budget_reset':False,
            'analysis_handoff':'Use campaign-state task.batch to locate batches/<batch> and call analysis_overlay.',
            'interpretation':'Assessed means the permitted repair routes were processed; it does not mean all sources or semantic gaps were recovered.'})
        return state


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--seed',type=Path)
    parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    run_campaign(args.archive,args.output,seed=args.seed,network=not args.offline)


if __name__=='__main__':main()
