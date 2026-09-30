"""Local freshness watchdog: dispatch GitHub polling only; never collect locally."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from ForecastAgent.runtime.task_lock import task_lock

REPO='chenencc/futureeval-forecasting-agent'
WORKFLOW='run_bot_on_tournament.yaml'


def instant(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)


def decide(runs, capture_time, now, last_dispatch=None, interval_seconds=1200):
    active=[r for r in runs if r['status']!='completed']
    if active:
        age=(now-min(instant(r['created_at']) for r in active)).total_seconds()
        return {'action':'wait','reason':'existing_poll_active','needs_attention':age>1800,
                'active_run_ids':[r['id'] for r in active],'active_age_seconds':age}
    age=(now-instant(capture_time)).total_seconds() if capture_time else None
    if age is not None and age<interval_seconds:
        return {'action':'none','reason':'fresh_snapshot','snapshot_age_seconds':age,'needs_attention':False}
    if last_dispatch and (now-instant(last_dispatch)).total_seconds()<interval_seconds:
        return {'action':'wait','reason':'dispatch_cooldown','needs_attention':False,'snapshot_age_seconds':age}
    return {'action':'dispatch','reason':'snapshot_overdue','snapshot_age_seconds':age,'needs_attention':False}


def gh_call(executable,args, *,json_output=True):
    env=dict(os.environ,GH_PROMPT_DISABLED='1',GH_NO_UPDATE_NOTIFIER='1')
    result=subprocess.run([str(executable),*args],capture_output=True,text=True,encoding='utf-8',
                          errors='replace',timeout=45,env=env)
    if result.returncode: raise RuntimeError('GitHub CLI request failed: '+result.stderr[:500])
    return json.loads(result.stdout) if json_output else result.stdout


def run_watchdog(root,executable,*,apply=False):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with task_lock(root):
        path=root/'status.json'
        previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        now=datetime.now(timezone.utc)
        try:
            response=gh_call(executable,['api',f'repos/{REPO}/actions/workflows/{WORKFLOW}/runs?branch=main&per_page=100'])
            runs=response['workflow_runs']
            successful=next((r for r in runs if r.get('conclusion')=='success' and r['status']=='completed'),None)
            capture=None
            if successful:
                artifacts=gh_call(executable,['api',f'repos/{REPO}/actions/runs/{successful["id"]}/artifacts?per_page=100'])
                if any(a['name']==f'futureeval-questions-{successful["id"]}' and not a['expired'] for a in artifacts['artifacts']):
                    jobs=gh_call(executable,['api',f'repos/{REPO}/actions/runs/{successful["id"]}/jobs?per_page=100'])
                    captures=[s['completed_at'] for j in jobs['jobs'] for s in j.get('steps',[])
                              if s['name']=='Save read-only question snapshot' and s['conclusion']=='success' and s.get('completed_at')]
                    capture=max(captures) if captures else None
            decision=decide(runs,capture,now,previous.get('last_dispatch_at_utc'))
            state={**decision,'checked_at_utc':now.isoformat(),'last_successful_capture_at_utc':capture,
                   'last_successful_run_id':successful['id'] if successful else None,
                   'last_dispatch_at_utc':previous.get('last_dispatch_at_utc'),
                   'repo':REPO,'workflow':WORKFLOW,'executor':'GitHub Actions','local_collection':False,'applied':apply}
            if decision['action']=='dispatch' and apply:
                # Persist reservation before an uncertain dispatch outcome; prevent storms.
                state['last_dispatch_at_utc']=now.isoformat()
                atomic_write(path,state)
                gh_call(executable,['workflow','run',WORKFLOW,'--repo',REPO,'--ref','main'],json_output=False)
                state['action']='dispatched'
            atomic_write(path,state)
            return state
        except Exception as exc:
            state={**previous,'checked_at_utc':now.isoformat(),'action':'error','needs_attention':True,
                   'error':str(exc)[:800],'local_collection':False}
            atomic_write(path,state)
            raise


def atomic_write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    temporary.replace(path)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('E:/metaculus_data/monitor-watchdog'))
    parser.add_argument('--gh-path',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run_watchdog(args.root,args.gh_path,apply=args.apply)))
