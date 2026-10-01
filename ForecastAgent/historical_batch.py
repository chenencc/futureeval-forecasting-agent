"""Frozen historical collection campaigns with bounded, resumable small batches."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

from ForecastAgent.runtime.retrieval import run_retrieval, parse_time
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.evidence.temporal import timestamp

DEFAULT_INPUT = Path(__file__).parent / 'fixtures' / 'historical_2026_135.json'
ALLOWED = {'id', 'question', 'background', 'resolution_criteria', 'as_of_utc',
           'tavily_end_date', 'date_basis', 'historical_criteria_audit'}


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def initialize(root, input_path=DEFAULT_INPUT, profile='collection_v1'):
    if profile not in {'collection_v1','collection_v2','collection_v3'}:
        raise ValueError('Unknown acquisition profile')
    root = Path(root)
    rows = json.loads(Path(input_path).read_text(encoding='utf-8'))
    if not isinstance(rows, list) or not rows:
        raise ValueError('Input must be a nonempty blind question list')
    ids = []
    for row in rows:
        if set(row) - ALLOWED:
            raise ValueError('Unexpected input fields: outcome labels must remain separate')
        ident = str(row.get('id', ''))
        if not ident.isdecimal() or ident in ids:
            raise ValueError('Question IDs must be unique numeric identifiers')
        if not row.get('question') or not row.get('resolution_criteria') or not timestamp(row.get('as_of_utc')):
            raise ValueError('Question, criteria and timezone-aware cutoff are required')
        ids.append(ident)
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        path = root / 'batch.json'
        if path.exists():
            batch = json.loads(path.read_text(encoding='utf-8'))
            if batch['input_sha256'] != digest(rows):
                raise ValueError('Frozen campaign inputs changed; resume refused')
            if batch.get('acquisition_profile','collection_v1')!=profile:
                raise ValueError('Frozen acquisition profile changed; resume refused')
            validate(root, batch)
            return batch
        write(root / 'blind_inputs.json', rows)
        batch = {'schema': 'historical_collection_batch_v1', 'created_at_utc': now().isoformat(),
                 'acquisition_profile':profile,
                 'input_sha256': digest(rows), 'mode': 'historical_exploratory', 'pipeline': 'collection',
                 'limits': {'questions_per_run': 5, 'attempts_per_question': 5,
                            'tavily_basic_lifetime': 5 if profile=='collection_v2' else 3, 'model_http_lifetime': 72, 'run_seconds_per_question': 900},
                 'tasks': {ident: {'status': 'pending', 'attempts': []} for ident in ids}}
        write(path, batch)
        return batch


def validate(root, batch):
    rows = json.loads((root / 'blind_inputs.json').read_text(encoding='utf-8'))
    if digest(rows) != batch['input_sha256'] or list(batch['tasks']) != [str(row['id']) for row in rows]:
        raise ValueError('Campaign manifest or frozen inputs failed integrity validation')
    return {str(row['id']): row for row in rows}


def run_batch(root, tavily_key, router_key, limit=5, runner=run_retrieval):
    if not 1 <= limit <= 5:
        raise ValueError('Run size must be between one and five')
    root = Path(root)
    with task_lock(root):
        batch = json.loads((root / 'batch.json').read_text(encoding='utf-8'))
        rows = validate(root, batch)
        selected = 0
        for ident, entry in batch['tasks'].items():
            directory = root / 'tasks' / ident
            bundle_path = directory / 'bundle.json'
            if bundle_path.exists():
                bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
                if bundle.get('result') and not bundle['result'].get('incomplete'):
                    entry['status'] = 'complete'
                    continue
                if len(bundle.get('model_attempts', [])) >= 72:
                    entry['status'] = 'blocked_budget'
                    continue
            elif entry['attempts']:
                # A lost task ledger could otherwise reset its search budget.
                entry['status'] = 'missing_task_state'
                continue
            if len(entry['attempts']) >= 5:
                entry['status'] = 'needs_attention'
                continue
            retry = parse_time(entry.get('retry_after_utc'))
            if retry and retry > now():
                continue
            if selected >= limit:
                break
            selected += 1
            request = {**rows[ident], 'mode': batch['mode'], 'pipeline': batch['pipeline']}
            if batch.get('acquisition_profile') in {'collection_v2','collection_v3'}:
                request['acquisition_profile']=batch['acquisition_profile']
            # Freeze the empty task before reserving an execution, so a crash
            # cannot ambiguously lose previously consumed search reservations.
            from ForecastAgent.runtime.retrieval import RetrievalTask
            directory.mkdir(parents=True, exist_ok=True)
            with task_lock(directory):
                RetrievalTask(directory, request).save()
            attempt = {'started_at_utc': now().isoformat(), 'status': 'reserved',
                       'code_commit': os.environ.get('GITHUB_SHA')}
            entry['attempts'].append(attempt)
            entry['status'] = 'running'
            write(root / 'batch.json', batch)
            print(json.dumps({'question_id':ident,'stage':'starting','profile':batch.get('acquisition_profile'),
                              'attempt':len(entry['attempts'])}),flush=True)
            try:
                bundle = runner(request, directory, tavily_key, router_key)
                result = bundle.get('result') or {}
                entry['status'] = 'incomplete' if result.get('incomplete') or not result else 'complete'
                entry['collection_status'] = result.get('status')
                attempt['status'] = entry['status']
            except Exception as exc:
                entry['status'] = 'failed'
                detail = str(exc)
                for secret in (tavily_key, router_key):
                    if secret:
                        detail = detail.replace(secret, '[REDACTED]')
                attempt.update(status='failed', error=type(exc).__name__, detail=detail[:500])
            attempt['finished_at_utc'] = now().isoformat()
            if entry['status'] != 'complete':
                entry['retry_after_utc'] = (now() + timedelta(minutes=min(240, 20 * 2 ** (len(entry['attempts']) - 1)))).isoformat()
            write(root / 'batch.json', batch)
            print(json.dumps({'question_id':ident,'stage':entry['status'],'collection_status':entry.get('collection_status')}),flush=True)
        write(root / 'batch.json', batch)
        return batch


def enable_exa(root, authorization_id, reason):
    """Apply an explicit, auditable one-attempt supplement without restarting ledgers."""
    if not authorization_id or not reason:
        raise ValueError('Explicit authorization ID and reason required')
    root=Path(root)
    with task_lock(root):
        batch=json.loads((root/'batch.json').read_text(encoding='utf-8'))
        validate(root,batch)
        baseline={}
        for ident in batch['tasks']:
            path=root/'tasks'/ident/'bundle.json'
            if not path.exists():
                raise ValueError('Every prior task ledger must exist before a supplement')
            b=json.loads(path.read_text(encoding='utf-8'))
            baseline[ident]={'model_http_attempts':len(b.get('model_attempts',[])),
                'known_tokens':sum((a.get('usage') or {}).get('total_tokens',0) for a in b.get('model_attempts',[])),
                'tavily_basic_attempts':len(b['searches']),'exa_search_attempts':len(b.get('exa_searches',[])),
                'request_hash':b['request_hash'],'result':b.get('result')}
        baseline_path=root/'repair_resume_baseline.json'
        if not baseline_path.exists():
            write(baseline_path,{'source_run':os.environ.get('FORECAST_COMPLETED_RESTORE_RUNS'),
                'authorized_at_utc':now().isoformat(),'authorization_id':authorization_id,
                'reason':reason,'tasks':baseline,'comparison':'Resume increments are not a fresh-run A/B comparison.'})
        from ForecastAgent.tools.channels import channel_catalog
        granted=[]
        for ident in batch['tasks']:
            directory=root/'tasks'/ident
            with task_lock(directory):
                path=directory/'bundle.json'
                b=json.loads(path.read_text(encoding='utf-8'))
                if b.get('result') and not b['result'].get('incomplete'):
                    continue
                limits=b.setdefault('acquisition_limits',{})
                if limits.get('exa_search',0)==1:
                    continue
                if b.get('exa_searches'):
                    raise ValueError('Exa attempt already consumed without a matching allowance')
                limits['exa_search']=1
                b.setdefault('exa_searches',[])
                b.setdefault('budget_amendments',[]).append({'provider':'exa','from':0,'to':1,
                    'authorization_id':authorization_id,'reason':reason,'at_utc':now().isoformat(),
                    'prior_counters':baseline[ident],'tavily_budget_reset':False,'model_budget_reset':False})
                b['channel_catalog']=channel_catalog()
                write(path,b)
                granted.append(ident)
        return {'enabled_tasks':granted,'baseline_path':str(baseline_path)}


def archive(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination == root or root in destination.parents:
        raise ValueError('Archive must be outside campaign directory')
    batch = json.loads((root / 'batch.json').read_text(encoding='utf-8'))
    validate(root, batch)
    files = [p for p in root.rglob('*') if p.is_file() and p.name not in
             {'.running.lock', '.lock.recovery', 'archive_manifest.json'} and p.suffix != '.tmp']
    manifest = {str(p.relative_to(root)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in files:
            output.write(path, str(path.relative_to(root)))
        output.writestr('archive_manifest.json', json.dumps(manifest, indent=2))
    temporary.replace(destination)
    return {'path': str(destination), 'files': len(files), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init', 'run', 'status', 'archive','enable-exa'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--profile',choices=['collection_v1','collection_v2','collection_v3'],default='collection_v1')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--authorization-id')
    parser.add_argument('--reason')
    args = parser.parse_args()
    if args.action == 'init':
        batch = initialize(args.root, args.input,args.profile)
    elif args.action == 'run':
        if not os.environ.get('TAVILY_API_KEY') or not os.environ.get('OPENROUTER_API_KEY'):
            raise ValueError('Both provider keys are required')
        batch = run_batch(args.root, os.environ['TAVILY_API_KEY'], os.environ['OPENROUTER_API_KEY'], args.limit)
    elif args.action == 'enable-exa':
        print(json.dumps(enable_exa(args.root,args.authorization_id,args.reason)))
        return
    elif args.action == 'archive':
        if not args.output:
            parser.error('--output is required for archive')
        print(json.dumps(archive(args.root, args.output)))
        return
    else:
        batch = json.loads((args.root / 'batch.json').read_text(encoding='utf-8'))
        validate(args.root, batch)
    counts = {}
    for entry in batch['tasks'].values():
        counts[entry['status']] = counts.get(entry['status'], 0) + 1
    print(json.dumps({'questions': len(batch['tasks']), 'states': counts}))


if __name__ == '__main__':
    main()
