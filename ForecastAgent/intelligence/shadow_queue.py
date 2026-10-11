"""Development queue; reuse frozen platform rules without editing release code."""
from pathlib import Path
from datetime import timedelta
import time

from ForecastAgent.competition import live
from ForecastAgent.competition.platform import has_existing_forecast
from ForecastAgent.competition.queue import load, save, questions, descriptor, utc
from ForecastAgent.runtime.task_lock import task_lock

SCHEMA='official-competition-v1-graph-shadow'
TERMINAL=live.TERMINAL | {'shadow_scored'}


def run(root,snapshots,*,client,adapter,limit=5):
    if type(limit) is not int or not 1<=limit<=5:raise ValueError('At most five questions per dispatch')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    account=client.account()
    with task_lock(root):
        path=root/'campaign.json'
        if not path.exists() and (root/'tasks').exists():
            raise ValueError('Shadow tasks exist without ledger; refuse quota restart')
        state=load(path) if path.exists() else {'schema':SCHEMA,'tournament':'fall-futureeval-2026','tasks':{}}
        if state['schema']!=SCHEMA or state['tournament']!='fall-futureeval-2026':
            raise ValueError('Wrong shadow campaign identity')
        save(root/'account-check.json',account)
        indexes=[p for p in Path(snapshots).rglob('index.json')
                 if load(p).get('tournament')==state['tournament']]
        if not indexes:raise ValueError('No tournament snapshot found')
        index_path=max(indexes,key=lambda p:load(p)['retrieved_at_utc']);index=load(index_path)
        if not index.get('open_scan_complete'):raise ValueError('Incomplete open scan')
        if not 0<=(utc()-utc(index['retrieved_at_utc'])).total_seconds()<=7200:
            raise ValueError('Incoming shadow snapshot is stale')
        for row in index['questions']:
            if not row.get('open'):continue
            ident=str(row['question_id'])
            if not ident.isdecimal() or type(row['post_id']) is not int:raise ValueError('Invalid question identity')
            if ident in state['tasks']:continue
            post=load(index_path.parent/f"{row['post_id']}.json")['post']
            q=next((q for q in questions(post) if str(q['id'])==ident),None)
            if q is None or post['id']!=row['post_id']:raise ValueError('Incoming post/question mismatch')
            state['tasks'][ident]={'id':ident,'post_id':str(row['post_id']),'stage':'queued',
                'collection_executions':0,'discovered_at_utc':utc().isoformat(),
                'deadline_utc':descriptor(post,q)['deadline_utc'],'history':[]}
        save(path,state)
        pending=[t for t in state['tasks'].values() if t['stage'] not in TERMINAL and
                 (not t.get('retry_at_utc') or utc(t['retry_at_utc'])<=utc())]
        pending.sort(key=lambda t:t.get('deadline_utc') or index['retrieved_at_utc'])
        processed=[];problems=[];started=time.monotonic()
        for task in pending[:limit]:
            if time.monotonic()-started>3000:break
            ident=task['id'];folder=root/'tasks'/ident;folder.mkdir(parents=True,exist_ok=True)
            try:
                if (folder/'submission.json').exists():
                    raise ValueError('Production submission receipt found in shadow state')
                post=client.post(task['post_id'])
                question=next((q for q in questions(post) if str(q['id'])==ident),None)
                if question is None:raise ValueError('Registered question missing')
                task['deadline_utc']=descriptor(post,question)['deadline_utc']
                if question.get('status')!='open':task['stage']='closed';continue
                if not task['deadline_utc'] or utc(task['deadline_utc'])<=utc():
                    task['stage']='deadline_missed';continue
                if has_existing_forecast(question):task['stage']='already_forecasted';continue
                identity=live.rule_identity(post,question)
                if task.get('rule_identity') and task['rule_identity']!=identity:
                    raise ValueError('Frozen live rules changed')
                task['rule_identity']=identity
                request=live.live_request(post,question)
                live.current(client,task)
                candidate_path=folder/'candidate.json'
                if candidate_path.exists():candidate=load(candidate_path)
                else:
                    retrieval=folder/'retrieval';bundle_path=retrieval/'bundle.json'
                    bundle=load(bundle_path) if bundle_path.exists() else None
                    if not bundle or not bundle.get('result') or bundle['result'].get('incomplete'):
                        if task['collection_executions']>=3:
                            raise RuntimeError('Collection lifetime execution cap exhausted')
                        task['collection_executions']+=1;task['stage']='collecting';save(path,state)
                        bundle=adapter.collect(request,retrieval)
                    if not bundle.get('result') or bundle['result'].get('incomplete'):
                        raise RuntimeError('Collection interrupted; original budgets preserved')
                    live.current(client,task)
                    source=folder/'analysis-input.json'
                    if not source.exists():
                        task['stage']='supplementing';save(path,state)
                        save(source,adapter.supplement(bundle,folder,ident))
                    live.current(client,task)
                    task['stage']='analyzing';save(path,state)
                    candidate=adapter.infer(source,folder,ident)
                live.current(client,task)
                receipt=adapter.deliver(client,task,candidate['payload'],candidate['comment'],folder,enabled=False)
                if receipt['status']!='shadow_scored' or receipt.get('submitted') is not False:
                    raise ValueError('Shadow result cannot be a production submission')
                task.update(stage='shadow_scored',retry_at_utc=None)
            except Exception as exc:
                message=str(exc)
                stage='blocked_integrity' if isinstance(exc,ValueError) else 'retry_wait'
                if 'cap exhausted' in message.lower() or 'quota' in message.lower() or 'provider review required' in message.lower():
                    stage='provider_blocked'
                elif 'closed' in message.lower() or 'deadline' in message.lower():stage='deadline_missed'
                task.update(stage=stage,last_error=type(exc).__name__+': '+message,
                    retry_at_utc=(utc()+timedelta(minutes=10)).isoformat())
                save(folder/'failure.json',{'stage':stage,'error':task['last_error'],'preserved_state':True})
                problems.append({'id':ident,'stage':stage,'error':task['last_error']})
            finally:
                task['history'].append({'at_utc':utc().isoformat(),'stage':task['stage']})
                processed.append(ident);save(path,state)
        counts={}
        for task in state['tasks'].values():counts[task['stage']]=counts.get(task['stage'],0)+1
        report={'schema':SCHEMA,'enabled':False,'shadow':True,'submitted':False,
            'snapshot_at_utc':index['retrieved_at_utc'],'state_distribution':counts,
            'processed_ids':processed,'problems':problems,'finished_at_utc':utc().isoformat(),
            'selection_policy':'One Mercury score; original-only if no graph; one bounded original-evidence reasoning fallback after a known completed service failure',
            'probability_policy':live.POLICY}
        save(root/'report.json',report);return report
