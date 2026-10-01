"""Bounded current-information acquisition campaigns with durable task accounting."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval, parse_time
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.runtime.limits import MODEL_HTTP_PER_DISPATCH
from ForecastAgent.providers.model import configured_model


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def prepare(root, fixture, count=100, raw_recall=False):
    rows = read(Path(fixture))
    if type(count) is not int or not 1 <= count <= 100 or len(rows) < count:
        raise ValueError('Select one to 100 questions from a sufficiently large fixture')
    selected = rows[:count]
    requests = {}
    for row in selected:
        ident = str(row.get('id', ''))
        if not ident.isdecimal() or ident in requests:
            raise ValueError('Unique numeric question IDs are required')
        if set(row) & {'resolution', 'resolved_to', 'assessment', 'probability'}:
            raise ValueError('Outcome labels must remain outside acquisition inputs')
        if not row.get('question') or not row.get('resolution_criteria'):
            raise ValueError('Full question and resolution criteria are required')
        requests[ident] = {k: row[k] for k in ('id', 'question', 'background', 'resolution_criteria') if k in row}
        requests[ident].update(mode='live', pipeline='collection', acquisition_profile='collection_v3')
        if raw_recall:
            requests[ident]['acquisition_focus']='raw_recall'
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        path = root / 'campaign.json'
        if path.exists():
            campaign = read(path)
            if campaign['input_sha256'] != digest(selected) or campaign['requests'] != requests:
                raise ValueError('Frozen campaign inputs changed; refusing a budget reset')
            return campaign
        campaign = {'schema': 'current_collection_campaign_v1', 'created_at_utc': now().isoformat(),
            'input_sha256': digest(selected), 'requests': requests, 'model': configured_model(),
            'policy': 'Current-information acquisition only; original dates are provenance, not cutoffs.',
            'forecast_submissions': False, 'limits': {'tasks_per_dispatch': 5,
                'executions_per_task': 3, 'model_http_campaign': count * MODEL_HTTP_PER_DISPATCH,
                'tavily_basic_per_task_lifetime': 3, 'exa_per_task_lifetime': 1},
            'tasks': {ident: {'status': 'pending', 'attempts': [], 'resources': {}} for ident in requests},
            'acquisition_focus':'raw_recall' if raw_recall else 'associated_material',
            'scheduler':{'max_active_tasks':1,'dispatch_limit':5,'retry_policy':'classified_only',
                'outage_task_threshold':2,'budget_reset':False}}
        write(root / 'original_inputs.json', selected)
        write(path, campaign)
        return campaign


def inspect_task(bundle):
    result = bundle.get('result') or {}
    if (len(bundle.get('model_attempts', [])) > 72 or len(bundle.get('searches', [])) > 3 or len(bundle.get('exa_searches', [])) > 1
            or len(bundle.get('fetch_attempts',[]))>8 or len(bundle.get('extract_attempts',[]))>1):
        raise ValueError('Consumed task budget exceeds the campaign contract')
    if bundle.get('submitted_to_metaculus'):
        raise ValueError('Forecast submission is outside this campaign')
    if result and not result.get('incomplete'):
        status = 'acquired' if result.get('acquisition_complete') and bundle.get('acceptance', {}).get('status') == 'accepted' else 'closed_with_gaps'
    elif len(bundle.get('model_attempts', [])) >= 72:
        status = 'blocked_budget'
    else:
        status = 'incomplete'
    from ForecastAgent.runtime.batch_health import task_summary
    summary=task_summary(bundle)
    if bundle.get('request',{}).get('acquisition_focus')=='raw_recall' and result and not result.get('incomplete'):
        from ForecastAgent.evidence.acceptance import collection_acceptance
        audited=collection_acceptance(bundle)
        if audited['status']=='failed':
            status='needs_attention'
        elif audited['status']!='accepted':
            status='closed_with_gaps'
        elif summary['failed_source_count'] or summary['failed_search_count']:
            status='closed_with_gaps'
    return {'status': status, 'quality_inventory':summary, 'resources': {'model_http': len(bundle.get('model_attempts', [])),
        'tavily_basic': len(bundle.get('searches', [])), 'exa': len(bundle.get('exa_searches', [])),
        'reported_tokens': sum((a.get('usage') or {}).get('total_tokens', 0) for a in bundle.get('model_attempts', [])),
        'usage_missing': sum(not bool(a.get('usage')) for a in bundle.get('model_attempts', []))},
        'acceptance': bundle.get('acceptance', {}).get('status'),
        'gaps': result.get('gaps', []), 'acquisition_complete': bool(result.get('acquisition_complete'))}


def reconcile(root, campaign):
    original = read(root / 'original_inputs.json')
    expected = {}
    for row in original:
        request = {k: row[k] for k in ('id', 'question', 'background', 'resolution_criteria') if k in row}
        request.update(mode='live', pipeline='collection', acquisition_profile='collection_v3')
        if campaign.get('acquisition_focus')=='raw_recall':
            request['acquisition_focus']='raw_recall'
        expected[str(row['id'])] = request
    if digest(original) != campaign['input_sha256'] or expected != campaign['requests'] or set(expected) != set(campaign['tasks']):
        raise ValueError('Frozen source inputs failed integrity validation')
    for ident, entry in campaign['tasks'].items():
        path = root / 'tasks' / ident / 'bundle.json'
        if not path.exists():
            if entry['attempts']:
                raise ValueError('Consumed task state missing: ' + ident)
            continue
        bundle = read(path)
        if bundle['request_hash'] != digest(campaign['requests'][ident]):
            raise ValueError('Task input fingerprint changed: ' + ident)
        from ForecastAgent.runtime.batch_health import transport_records
        transport_records(path.parent,bundle.get('model_attempts',[]))
        prior_status=entry['status']
        prior_resources=dict(entry.get('resources',{}))
        entry.update(inspect_task(bundle))
        if any(entry['resources'].get(k,0)<prior_resources.get(k,0) for k in ('model_http','tavily_basic','exa')):
            raise ValueError('Consumed resource ledger regressed: '+ident)
        if prior_status=='needs_attention':
            entry['status']='needs_attention'
        if prior_status=='running' and entry['status']=='incomplete':
            # A reservation survived owner loss. Resume the same ledger later;
            # never repeat a reserved provider request as a fresh attempt.
            from ForecastAgent.runtime.batch_health import retry_minutes
            entry['failure']={'category':'owner_interrupted','retryable':True,'pause_batch':False}
            entry.setdefault('retry_after_utc',(now()+timedelta(minutes=retry_minutes(len(entry['attempts'])))).isoformat())
            if entry['attempts'] and entry['attempts'][-1]['status']=='reserved':
                entry['attempts'][-1]['status']='owner_interrupted'
    if report(campaign)['resources']['model_http'] > campaign['limits']['model_http_campaign']:
        raise ValueError('Consumed model budget exceeds campaign ceiling')
    return campaign


def report(campaign):
    from ForecastAgent.runtime.batch_health import completion_state
    return {'schema': 'collection_campaign_status_v1', 'questions': len(campaign['tasks']),
        'states': dict(Counter(e['status'] for e in campaign['tasks'].values())),
        'completion_states':dict(Counter(completion_state(e) for e in campaign['tasks'].values())),
        'task_reports':[{ 'question_id':ident,'completion_state':completion_state(entry),
            'attempts':len(entry['attempts']),'resources':entry.get('resources',{}),
            'failure':entry.get('failure'),'retry_after_utc':entry.get('retry_after_utc'),
            'quality_inventory':entry.get('quality_inventory'),
            'bundle_path':'tasks/'+ident+'/bundle.json' if entry['attempts'] else None}
            for ident,entry in campaign['tasks'].items()],
        'failure_categories':dict(Counter(e['failure']['category'] for e in campaign['tasks'].values() if e.get('failure'))),
        'requires_provider_review':campaign.get('requires_provider_review',False),
        'resources': {key: sum(e.get('resources', {}).get(key, 0) for e in campaign['tasks'].values())
            for key in ('model_http', 'tavily_basic', 'exa', 'reported_tokens', 'usage_missing')},
        'limits': campaign['limits'], 'pause_until_utc': campaign.get('pause_until_utc'),
        'stop_reason': campaign.get('stop_reason'),
        'pause_reason':campaign.get('pause_reason'),'scheduler':campaign.get('scheduler',{'max_active_tasks':1}),
        'scope': 'Acquisition closure and mechanical capture acceptance; not forecast accuracy.'}


def unavailable(directory, attempts):
    from ForecastAgent.runtime.batch_health import transport_records
    try:
        records=transport_records(directory,attempts)
    except (ValueError,OSError):
        return False  # Classified as integrity failure by the caller.
    return sum(r.get('http_status') in {429,500,502,503,504} for r in records)>=2


def run_batch(root, limit=1, runner=run_retrieval, question_id=None, resume_reason='', question_ids=None):
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('Each dispatch may process one to five questions')
    if not all(os.environ.get(k) for k in ('OPENROUTER_API_KEY', 'TAVILY_API_KEY', 'EXA_API_KEY')):
        raise ValueError('All acquisition provider credentials are required')
    root = Path(root)
    with task_lock(root):
        campaign = reconcile(root, read(root / 'campaign.json'))
        if campaign['model'] != configured_model():
            raise ValueError('Frozen campaign model differs from the configured backend')
        if question_ids is not None:
            question_ids = [str(ident) for ident in question_ids]
            if question_id is not None or not question_ids or len(question_ids) > limit or len(set(question_ids)) != len(question_ids):
                raise ValueError('Select unique new question IDs within the dispatch limit, without a repair selection')
            for ident in question_ids:
                if ident not in campaign['tasks'] or campaign['tasks'][ident]['attempts'] or campaign['tasks'][ident]['status'] != 'pending':
                    raise ValueError('New-case selection requires untouched pending task state: ' + ident)
        pause = parse_time(campaign.get('pause_until_utc'))
        if pause and pause > now():
            return report(campaign)
        if campaign.get('requires_provider_review'):
            return report(campaign)
        # New work first: repeated failures must not starve the rest of the queue.
        if question_ids is not None:
            candidates = question_ids
            campaign.setdefault('dispatch_selections', []).append({'question_ids': question_ids,
                'at_utc': now().isoformat(), 'code_commit': os.environ.get('GITHUB_SHA'),
                'selection_policy': 'Explicit unstarted queue tasks; no replacement inputs or provider budget reset.'})
            write(root / 'campaign.json', campaign)
        elif question_id is not None:
            question_id = str(question_id)
            if question_id not in campaign['tasks'] or not campaign['tasks'][question_id]['attempts'] or len(resume_reason.strip()) < 20:
                raise ValueError('Focused repair requires an existing consumed task and a substantive resume reason')
            operation = digest([question_id, os.environ.get('GITHUB_SHA', 'local'), resume_reason])
            if any(e['operation'] == operation for e in campaign.get('repair_resumptions', [])):
                raise ValueError('This focused repair already ran; refusing automatic repetition')
            campaign.setdefault('repair_resumptions', []).append({'operation': operation,
                'question_id': question_id, 'reason': resume_reason, 'at_utc': now().isoformat(),
                'prior_resources': dict(campaign['tasks'][question_id]['resources']), 'budget_reset': False})
            write(root / 'campaign.json', campaign)
            if campaign['tasks'][question_id]['status']=='needs_attention':
                # A human-selected repair may reopen a terminal task after
                # reconciliation. Execution and provider limits still apply.
                campaign['tasks'][question_id]['status']='incomplete'
            candidates = [question_id]
        else:
            candidates = sorted(campaign['tasks'], key=lambda ident: len(campaign['tasks'][ident]['attempts']))
        selected = 0
        for ident in candidates:
            entry = campaign['tasks'][ident]
            if entry['status'] in {'acquired', 'closed_with_gaps', 'blocked_budget', 'needs_attention'}:
                continue
            if len(entry['attempts']) >= campaign['limits']['executions_per_task']:
                entry['status'] = 'needs_attention'
                entry['failure']={'category':'execution_limit','retryable':False,'pause_batch':False}
                continue
            retry = parse_time(entry.get('retry_after_utc'))
            if retry and retry > now() and question_id is None:
                continue
            total = report(campaign)['resources']['model_http']
            if total + MODEL_HTTP_PER_DISPATCH > campaign['limits']['model_http_campaign']:
                campaign['stop_reason'] = 'campaign_model_budget'
                break
            if selected >= limit:
                break
            directory = root / 'tasks' / ident
            directory.mkdir(parents=True, exist_ok=True)
            with task_lock(directory):
                task = RetrievalTask(directory, campaign['requests'][ident])
                if task.bundle['acquisition_limits']['tavily_basic'] != 3 or task.bundle['acquisition_limits']['exa_search'] != 1:
                    raise ValueError('Task search quotas differ from campaign policy')
                task.save()
            baseline = inspect_task(task.bundle)['resources']['model_http']
            discovery_before={k:len(task.bundle.get(k,[])) for k in ('searches','exa_searches')}
            attempt = {'started_at_utc': now().isoformat(), 'status': 'reserved', 'model_http_before': baseline,
                       'code_commit': os.environ.get('GITHUB_SHA')}
            entry['attempts'].append(attempt)
            entry['status'] = 'running'
            write(root / 'campaign.json', campaign)
            selected += 1
            execution_error=None
            try:
                runner(campaign['requests'][ident], directory, os.environ['TAVILY_API_KEY'], os.environ['OPENROUTER_API_KEY'])
                entry.update(inspect_task(read(directory / 'bundle.json')))
                attempt['status'] = entry['status']
            except Exception as error:
                execution_error=error
                detail = str(error)
                for key in ('OPENROUTER_API_KEY', 'TAVILY_API_KEY', 'EXA_API_KEY'):
                    secret = os.environ.get(key)
                    if secret:
                        detail = detail.replace(secret, '[REDACTED]')
                attempt.update(status='failed', error=type(error).__name__, detail=detail[:500])
                entry.update(inspect_task(read(directory / 'bundle.json')))
                entry['status'] = 'incomplete'
            finally:
                attempt['finished_at_utc'] = now().isoformat()
                write(root / 'campaign.json', campaign)
            bundle = read(directory / 'bundle.json')
            from ForecastAgent.runtime.batch_health import failure,transport_records,retry_minutes,discovery_failures
            try:
                records=transport_records(directory,bundle.get('model_attempts',[])[baseline:])
            except (ValueError,OSError) as error:
                execution_error=error
                records=[]
                entry['status']='incomplete'
            records+=discovery_failures(bundle,discovery_before)
            if entry['status'] == 'incomplete':
                diagnosis=failure(execution_error,bundle,records)
                entry['failure']=diagnosis
                if diagnosis['retryable']:
                    entry['retry_after_utc'] = (now() + timedelta(minutes=retry_minutes(len(entry['attempts'])))).isoformat()
                else:
                    entry['status']='needs_attention'
                    entry.pop('retry_after_utc',None)
            elif entry['status'] in {'acquired','closed_with_gaps'}:
                entry.pop('failure',None)
                entry.pop('retry_after_utc',None)
            provider_error=failure(execution_error,bundle,records)
            outage=provider_error['category'] in {'provider_rate_limit','provider_unavailable','model_response_unavailable'} and (
                entry['status']=='incomplete' or any(r.get('status')=='discovery_failed' for r in records))
            campaign['consecutive_outage_tasks']=campaign.get('consecutive_outage_tasks',0)+1 if outage else 0
            if provider_error['pause_batch']:
                campaign['requires_provider_review']=True
                campaign['pause_reason']=provider_error['category']
            elif (unavailable(directory, bundle.get('model_attempts', [])[baseline:]) or campaign['consecutive_outage_tasks']>=2):
                campaign['pause_until_utc'] = (now() + timedelta(minutes=30)).isoformat()
                campaign['pause_reason'] = 'Repeated provider transport failures; preserve task and defer remaining queue.'
            attempt['status']=entry['status']
            write(root/'campaign.json',campaign)
            write(root/'status.json',report(campaign))
            if campaign.get('requires_provider_review') or (parse_time(campaign.get('pause_until_utc')) and parse_time(campaign['pause_until_utc'])>now()):
                break
        write(root / 'campaign.json', campaign)
        result = report(campaign)
        write(root / 'status.json', result)
        return result


def resume_provider(root,reason):
    """Explicit operator recovery; retain every task execution and provider quota."""
    if len(reason.strip())<20:
        raise ValueError('Document the provider fix or recovery before resuming')
    root=Path(root)
    with task_lock(root):
        campaign=reconcile(root,read(root/'campaign.json'))
        if not campaign.get('pause_until_utc') and not campaign.get('requires_provider_review'):
            raise ValueError('Campaign has no provider pause to clear')
        campaign.setdefault('provider_resumptions',[]).append({'at_utc':now().isoformat(),
            'reason':reason,'previous_pause_reason':campaign.get('pause_reason'),
            'prior_resources':report(campaign)['resources'],'budget_reset':False})
        campaign.pop('pause_until_utc',None)
        campaign.pop('pause_reason',None)
        campaign['requires_provider_review']=False
        campaign['consecutive_outage_tasks']=0
        write(root/'campaign.json',campaign)
        output=report(campaign);write(root/'status.json',output)
        return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run', 'status','resume-provider'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--limit', type=int, default=1)
    parser.add_argument('--question')
    parser.add_argument('--questions', help='Comma-separated IDs of untouched pending tasks')
    parser.add_argument('--resume-reason', default='')
    parser.add_argument('--raw-recall',action='store_true',help='Freeze raw acquisition focus when preparing a new campaign')
    args = parser.parse_args()
    if args.action == 'prepare':
        if not args.fixture:
            parser.error('--fixture is required')
        result = report(prepare(args.root, args.fixture, args.count,args.raw_recall))
    elif args.action == 'run':
        selection = args.questions.split(',') if args.questions is not None else None
        result = run_batch(args.root, args.limit, question_id=args.question, resume_reason=args.resume_reason, question_ids=selection)
    elif args.action=='resume-provider':
        result=resume_provider(args.root,args.resume_reason)
    else:
        with task_lock(args.root):
            result = report(reconcile(args.root, read(args.root / 'campaign.json')))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
