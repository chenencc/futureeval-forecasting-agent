"""New material acquisition plus the unchanged release 1.0.1 analysis core."""
import argparse
import copy
import hashlib
import json
import os
import sys
import time
import zipfile
from datetime import timedelta
from pathlib import Path

from ForecastAgent.acquisition import pipeline
from ForecastAgent.analysis.distributions import probability, validate_cdf, payload
from ForecastAgent.competition import live
from ForecastAgent.competition.tournaments import configured_tournament
from ForecastAgent.competition.queue import load, save, digest, questions, descriptor, utc
from ForecastAgent.releases.guard import execute
from ForecastAgent.releases import surfaces
from ForecastAgent.releases.manifest import verify
from ForecastAgent.runtime.task_lock import task_lock

VERSION='1.0.3'
CORE_VERSION='1.0.1'
STOPS={'program_dispatch_limit','model_dispatch_budget','stalled','program_forced_close',
       'repeated_tool_errors','review_round_limit'}


def verify_release():
    return verify(expected_version='1.0.3')


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_manifest(root):
    return {p.relative_to(root).as_posix():file_hash(p) for p in sorted(root.rglob('*')) if p.is_file()}


def raw_handoff_eligible(bundle):
    r=bundle.get('result') or {}; e=r.get('execution_report') or {}
    readable=any(p.get('content') and p.get('body_diagnostics',{}).get('usable_text')
                 for p in bundle.get('pages',{}).values())
    normal=r and not r.get('incomplete')
    closed=(r.get('status')=='collected' and r.get('termination_reason') in STOPS and e.get('interrupted') is False)
    local=(r.get('status')=='partial' and r.get('termination_reason')=='context_projection_failure' and
           e.get('owner')=='program' and e.get('interrupted') is True and bundle.get('model_attempts') and
           all(v.get('status')=='received' for v in bundle['model_attempts']))
    return bool(readable and (normal or closed or local))


def handoff(directory):
    """Advance raw export without changing any original collector file or result."""
    directory=Path(directory); state=load(directory/'state.json')
    if state['stage']!='collection':return False
    root=directory/'collection'; bundle=load(root/'bundle.json')
    service_gap=service_gap_eligible(bundle,root)
    if not raw_handoff_eligible(bundle) and not service_gap:return False
    frozen=load(directory/'identity.json')
    if pipeline.digest(frozen)!=state['identity_sha256'] or bundle['request']!=frozen['request']:
        raise ValueError('Original acquisition identity changed')
    for a in bundle.get('model_attempts',[]):
        path=root/a['path']
        if not path.resolve().is_relative_to(root.resolve()) or file_hash(path)!=a['sha256']:
            raise ValueError('Original acquisition journal changed')
    receipt={'version':VERSION,'original_state':state,'original_result':bundle['result'],
             'native_files_sha256':native_manifest(root),'budget_reset':False,
             'semantic_complete':False,'known_service_interruption':service_gap,
             'new_collection_calls':0,'new_search_calls':0}
    save(directory/'raw-handoff.json',receipt)
    ident=bundle['request']['id'];archive=directory/'parent.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('campaign.json',json.dumps({'tasks':{ident:{'status':'closed_with_gaps'}}}))
        for path in sorted(root.rglob('*')):
            if path.is_file():z.write(path,'tasks/'+ident+'/'+path.relative_to(root).as_posix())
    state.update(stage='supplement',parent_bundle_sha256=file_hash(root/'bundle.json'),
                 parent_archive_sha256=file_hash(archive),interrupted=False,
                 raw_only_handoff=True,collector_result_unchanged=True)
    save(directory/'state.json',state)
    return True


def service_gap_eligible(bundle,root):
    """Use saved readable material after completed service-error receipts only."""
    result=bundle.get('result') or {};execution=result.get('execution_report') or {}
    if not (result.get('status')=='partial' and result.get('termination_reason')=='model_transport_failure'
            and execution.get('owner')=='program' and execution.get('interrupted') is True):return False
    if not any(p.get('content') and p.get('body_diagnostics',{}).get('usable_text') for p in bundle.get('pages',{}).values()):return False
    failed=[a for a in bundle.get('model_attempts',[]) if a.get('status')!='received']
    if not failed:return False
    for attempt in failed:
        path=(Path(root)/attempt['path']).resolve()
        if not path.is_relative_to(Path(root).resolve()) or file_hash(path)!=attempt['sha256']:raise ValueError('Failed provider journal changed')
        record=load(path);error=(record.get('response') or {}).get('error') or {}
        code=record.get('http_status') or (error.get('code') if isinstance(error,dict) else None)
        if not isinstance(code,int) or code<500 or record.get('status')!=attempt['status']:return False
    return True


def collect(request, retrieval):
    verify_release()
    request=copy.deepcopy(request)
    request.update(acquisition_strategy='intelligent_materials_v3',drain_unseen_reads_before_stall=True,
                   recover_sources_before_stall=True,
                   source_recovery_policy_sha256=hashlib.sha256((Path(__file__).parents[1]/'runtime/source_frontier.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
                   exa_search_policy='required')
    directory=Path(retrieval)/'release-1.0.3'
    if not directory.exists() and Path(retrieval).exists() and any(Path(retrieval).iterdir()):
        raise ValueError('Previous acquisition files exist; preserve budgets and require explicit migration')
    if (directory/'state.json').exists() and (directory/'collection/bundle.json').exists():
        if load(directory/'identity.json')!=pipeline.identity(request,True):raise ValueError('Saved acquisition identity differs from the current request')
        handoff(directory)
    result=pipeline.run(request,directory,supplement_network=True)
    if result.get('state')!='complete' and handoff(directory):
        result=pipeline.run(request,directory,supplement_network=True)
    raw=load(directory/'collection/bundle.json')
    if result.get('state')!='complete':return raw
    receipt=directory/'raw-handoff.json'
    if receipt.exists() and native_manifest(directory/'collection')!=load(receipt)['native_files_sha256']:
        raise ValueError('Raw export changed native acquisition state')
    package=directory/'package.json'
    if receipt.exists() and raw['result'].get('incomplete'):
        exported=load(package);gaps=copy.deepcopy(exported.get('gaps',exported.get('result',{}).get('gaps',[])))
        gaps.append({'code':'collector_interrupted_or_budget_closed','raw_only_export':True,
                     'termination_reason':raw['result'].get('termination_reason'),
                     'description':'Original acquisition is incomplete. Saved readable material is available; missing observations are not evidence of nonoccurrence.'})
        exported.update(gaps=gaps,release_raw_export={'original_package_sha256':file_hash(package),
                         'collector_result_unchanged':True,'semantic_complete':False})
        package=directory/'raw-export-package.json'
        if package.exists() and load(package)!=exported:raise ValueError('Frozen raw export package changed')
        save(package,exported)
    # The legacy worker receives a stage-completion adapter, not a rewritten
    # collector result. Its supplement hook supplies the exact immutable package.
    adapter={'request':raw['request'],'result':{'status':'raw_package_exported','incomplete':False,
              'scope':'Downstream raw export only; original collector result is separately preserved.'},
             'release_acquisition':{'version':VERSION,'package_file':package.name,'package_sha256':file_hash(package)},
             'original_collector_result':raw['result']}
    save(Path(retrieval)/'bundle.json',adapter)
    return adapter


def supplement(adapter, folder, ident):
    directory=Path(folder)/'retrieval/release-1.0.3'
    marker=adapter.get('release_acquisition') or {}
    name=marker.get('package_file','package.json')
    if name not in {'package.json','raw-export-package.json'}:raise ValueError('Invalid material package filename')
    path=directory/name
    if marker.get('version')!=VERSION or file_hash(path)!=marker.get('package_sha256'):
        raise ValueError('Frozen material export adapter changed')
    package=load(path)
    if str(package['request']['id'])!=str(ident):raise ValueError('Wrong material package question')
    return package


def analyze(source, folder, ident):
    pipeline.verify_baseline()
    verify_release()
    folder=Path(folder);candidate_path=folder/'candidate.json'
    marker=folder/'candidate-integrity.json'
    if candidate_path.exists() and marker.exists():
        verify_candidate(folder);return load(candidate_path)
    core=folder/'analysis-core-1.0.1'
    try:candidate=live.analyze(source,core,ident)
    except Exception as exc:
        if not failed_provider_receipt(source,core):raise
        # A completed invalid/transport receipt already owns the only attempt.
        # Replay reaches the existing cap guard: retain a valid first decision
        # or use the unchanged reasoning fallback, without another Mercury HTTP.
        save(folder/'provider-recovery.json',{'error_type':type(exc).__name__,
             'replay_existing_journals':True,'new_mercury_attempts':0,'budget_reset':False})
        candidate=live.analyze(source,core,ident)
    candidate.update(release_version=VERSION,analysis_core_release_version=CORE_VERSION,
                     acquisition_release_version=VERSION)
    candidate['comment']=candidate['comment'].replace('# ForecastAgent 1.0.1', '# ForecastAgent 1.0.3')
    candidate['comment']+='\n\nAnalysis core: release 1.0.1. Raw collection completion does not certify evidence adequacy.'
    validate_payload(load(source)['request'],candidate['payload'])
    save(marker,{'source_file_sha256':file_hash(source),'candidate_sha256':digest(candidate),
                 'release_version':VERSION,'analysis_core_release_version':CORE_VERSION})
    save(candidate_path,candidate)
    return candidate


def failed_provider_receipt(source,core):
    from ForecastAgent.competition import mercury
    from ForecastAgent.analysis import pilot
    from ForecastAgent.providers import decisions
    root=Path(core)/'mercury-v1.0.1';identity=root/'identity.json'
    if not identity.exists():return False
    if load(identity).get('packet_sha256')!=pilot.digest(mercury.packet_for(load(source))):return False
    for stage in ('first','second'):
        request=root/stage/'request.json'
        if not request.exists() or (root/stage/'response.json').exists():continue
        for path in (root/stage/'http').glob('*.json'):
            receipt=load(path)
            if receipt.get('status')=='invalid_or_transport_error' and receipt.get('endpoint')==decisions.ENDPOINT and receipt.get('request')==load(request):
                return True
    return False


def validate_payload(question, candidate):
    kind=question['question_type']
    key='probability_yes' if kind=='binary' else ('probability_yes_per_category' if kind=='multiple_choice' else 'continuous_cdf')
    if set(candidate)!={'question',key} or type(candidate['question']) is not int or candidate['question']!=int(question['id']):
        raise ValueError('Wrong forecast identity or payload fields')
    if kind=='binary':
        if not .02<=probability(candidate[key])<=.98:raise ValueError('Binary clipping policy violated')
    elif kind=='multiple_choice':
        values=candidate[key]
        if set(values)!=set(question['options']) or abs(sum(values.values())-1)>1e-8:
            raise ValueError('Category labels or normalization violated')
        if any(not .02-1e-12<=probability(v)<=.98+1e-12 for v in values.values()):
            raise ValueError('Category clipping policy violated')
    else:validate_cdf(candidate[key],question,clipped=True)


def verify_candidate(folder):
    folder=Path(folder);marker=load(folder/'candidate-integrity.json')
    if digest(load(folder/'candidate.json'))!=marker['candidate_sha256']:
        raise ValueError('Saved candidate changed; refuse duplicate analysis or delivery')
    if file_hash(folder/'analysis-input.json')!=marker['source_file_sha256']:
        raise ValueError('Frozen candidate source changed')


def deliver(client,task,candidate,comment,folder,*,enabled=False):
    verify_candidate(folder);saved=load(Path(folder)/'candidate.json')
    if saved['payload']!=candidate or saved['comment']!=comment:
        raise ValueError('Delivery differs from the frozen candidate')
    validate_payload(load(Path(folder)/'analysis-input.json')['request'],candidate)
    task.update(worker_release_version=VERSION,analysis_release_version=VERSION,
                analysis_core_release_version=CORE_VERSION)
    return live.deliver(client,task,candidate,comment,folder,enabled=enabled)


def once(root, snapshots, *, enabled=False, client=None, infer=None, deliver_fn=None, collector=None, limit=1):
    snapshots=surfaces.snapshots(snapshots,Path(root)/'incoming-1.0.3')
    seed(root,snapshots)
    state=load(Path(root)/'campaign.json')
    for task in state['tasks'].values():
        folder=Path(root)/'tasks'/task['id']
        if task['stage'] not in live.TERMINAL and (folder/'candidate.json').exists():
            try:verify_candidate(folder)
            except (ValueError, FileNotFoundError) as exc:
                task.update(stage='blocked_integrity',last_error=str(exc))
    save(Path(root)/'campaign.json',state)
    client=surfaces.Client(client or live.Client(os.environ.get('METACULUS_TOKEN','')))
    report=live.run(root,snapshots,enabled=enabled,client=client,
                    collect=collector or collect,supplement=supplement,infer=infer or analyze,deliver_fn=deliver_fn or deliver,limit=limit)
    report.update(worker_release_version=VERSION,analysis_core_release_version=CORE_VERSION)
    save(Path(root)/'report.json',report)
    return report


def seed(root,snapshots):
    """Persist the complete question inventory before a network call can stall."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with task_lock(root):return _seed(root,snapshots)


def _seed(root,snapshots):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    indexes=[p for p in Path(snapshots).rglob('index.json') if load(p).get('tournament')==configured_tournament()]
    if not indexes:raise ValueError('Complete tournament snapshot required')
    ip=max(indexes,key=lambda p:load(p)['retrieved_at_utc']);index=load(ip)
    if not index.get('open_scan_complete') or not 0<=(utc()-utc(index['retrieved_at_utc'])).total_seconds()<=7200:
        raise ValueError('Incomplete or stale tournament scan')
    sp=root/'campaign.json'
    if not sp.exists() and (root/'tasks').exists():raise ValueError('Task files exist without ledger; never reset')
    state=load(sp) if sp.exists() else {'schema':live.SCHEMA,'tournament':configured_tournament(),'tasks':{}}
    if state.get('schema')!=live.SCHEMA or state.get('tournament')!=configured_tournament():
        raise ValueError('Wrong preserved campaign identity')
    for task in state['tasks'].values():
        if (task['stage'] not in live.TERMINAL and task.get('release_version') != VERSION and
                (task.get('collection_executions',0) or (root/'tasks'/task['id']).exists())):
            task.update(stage='blocked_integrity',last_error='Previous release has in-flight state; preserve budgets and require explicit migration')
        elif task['stage'] not in live.TERMINAL:
            task['release_version']=VERSION
    seen=[];surface_errors=load(ip.parent/'surface-errors.json') if (ip.parent/'surface-errors.json').exists() else {}
    for row in index['questions']:
        if not row.get('open'):continue
        ident=str(row['question_id'])
        if not ident.isdecimal() or type(row['post_id']) is not int:raise ValueError('Invalid question identity')
        post=load(ip.parent/(str(row['post_id'])+'.json'))['post']
        matched=[q for q in questions(post) if str(q['id'])==ident]
        if post['id']!=row['post_id'] or len(matched)!=1:raise ValueError('Incoming post identity mismatch')
        if ident in state['tasks'] and state['tasks'][ident]['post_id']!=str(row['post_id']):
            raise ValueError('Existing question belongs to another post')
        if ident not in state['tasks']:
            state['tasks'][ident]={'id':ident,'post_id':str(row['post_id']),'stage':'queued',
                'collection_executions':0,'history':[],'discovered_at_utc':utc().isoformat(),
                'deadline_utc':None,'release_version':VERSION}
        if state['tasks'][ident]['stage'] not in live.TERMINAL:
            try:
                state['tasks'][ident]['deadline_utc']=descriptor(post,matched[0])['deadline_utc']
                request=live.live_request(post,matched[0])
                if request['question_type']=='multiple_choice':
                    options=request['options'];payload(request,{'probability_yes_per_category':{o:1/len(options) for o in options}})
            except (ValueError,TypeError,KeyError) as exc:
                state['tasks'][ident].update(stage='blocked_integrity',last_error=str(exc))
        if ident in surface_errors and state['tasks'][ident]['stage'] not in live.TERMINAL:
            state['tasks'][ident].update(stage='blocked_integrity',last_error=surface_errors[ident])
        seen.append(ident)
    state['worker_release_version']=VERSION;save(sp,state)
    save(root/'coverage-inventory.json',{'input_question_ids':sorted(set(seen)),
         'recorded_question_ids':sorted(state['tasks']),'all_input_questions_recorded':all(q in state['tasks'] for q in seen)})
    return state


def due(state):
    tasks=[t for t in state['tasks'].values() if t['stage'] not in live.TERMINAL and
           (not t.get('retry_at_utc') or utc(t['retry_at_utc'])<=utc())]
    return sorted(tasks,key=lambda t:t.get('deadline_utc') or t['discovered_at_utc'])


def supervise(root,snapshots, *, submit=False, limit=5, task_seconds=1500, batch_seconds=6000,
              process_runner=execute):
    if not 1<=limit<=5 or not 1<=task_seconds<=1800 or not task_seconds<=batch_seconds<=6600:
        raise ValueError('Invalid bounded worker budget')
    root=Path(root).resolve();snapshots=Path(snapshots).resolve()
    (root/'.supervisor').mkdir(parents=True,exist_ok=True)
    with task_lock(root/'.supervisor'):
        snapshots=surfaces.snapshots(snapshots,root/'incoming-1.0.3')
        seed(root,snapshots);start=time.monotonic();attempts=[]
        if submit:
            for _ in range(limit):
                state=load(root/'campaign.json');pending=due(state)
                remaining=batch_seconds-(time.monotonic()-start)
                if not pending or remaining<=0:break
                task=pending[0];ident=task['id'];folder=root/'tasks'/ident
                if (folder/'candidate-integrity.json').exists() and (folder/'candidate.json').exists():
                    try:verify_candidate(folder)
                    except ValueError as exc:
                        task.update(stage='blocked_integrity',last_error=str(exc));save(root/'campaign.json',state)
                        attempts.append({'question_id':ident,'status':'blocked_integrity','error':str(exc)});continue
                command=[sys.executable,'-u','-m','ForecastAgent.releases.v1_0_3','--once',
                         '--root',str(root),'--snapshots',str(snapshots),'--submit']
                result=process_runner(command,root/'worker-logs'/f'{ident}-{len(attempts)}.log',
                                      min(task_seconds,remaining),cwd=str(pipeline.ROOT))
                result['question_id']=ident;attempts.append(result)
                if result['status']!='completed':
                    state=load(root/'campaign.json');task=state['tasks'][ident]
                    # Preserve accepted responses and unknown delivery receipts.
                    if task['stage'] not in live.TERMINAL:
                        task['supervisor_failures']=task.get('supervisor_failures',0)+1
                        task.update(stage='provider_blocked' if task['supervisor_failures']>=3 else 'retry_wait',
                            retry_at_utc=(utc()+timedelta(minutes=10)).isoformat(),
                            last_error='Bounded worker '+result['status']+'; all stage ledgers retained')
                        if (folder/'submission.json').exists():task['stage']='submission_unknown'
                    save(root/'campaign.json',state)
        state=load(root/'campaign.json');counts={}
        for t in state['tasks'].values():counts[t['stage']]=counts.get(t['stage'],0)+1
        report={'release_version':VERSION,'analysis_core_release_version':CORE_VERSION,
            'state_distribution':counts,'worker_attempts':attempts,'recorded_question_count':len(state['tasks']),
            'pending_due_ids':[t['id'] for t in due(state)],
            'attention_ids':[t['id'] for t in state['tasks'].values() if t['stage'] in {'blocked_integrity','provider_blocked','platform_rejected','submission_unknown'}],
            'no_budget_reset':True,'submission_enabled':submit}
        save(root/'supervisor-report.json',report)
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--snapshots',type=Path,required=True)
    parser.add_argument('--submit',action='store_true')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--limit',type=int,default=5)
    args=parser.parse_args();pipeline.verify_baseline();verify_release()
    if args.once:result=once(args.root,args.snapshots,enabled=args.submit)
    else:result=supervise(args.root,args.snapshots,submit=args.submit,limit=args.limit)
    print(json.dumps(result))


if __name__=='__main__':main()
