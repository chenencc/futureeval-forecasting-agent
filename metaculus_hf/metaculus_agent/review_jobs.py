"""Local, separately invoked evidence-review jobs; enqueue never calls a provider.

The review is a derived product. Capture/analysis/HF submission do not wait for it.
No automatic collection, uploads, deletion, or retry after uncertain process exit.
"""
import argparse
import json
import re
import time
from pathlib import Path
from pipeline_v2.repository import atomic_bytes, file_lock
from pipeline_v2.contracts import canonical, now
from . import independent_review as review


def save(path, value):
    atomic_bytes(path, (canonical(value)+'\n').encode())


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def enqueue(root, question, documents, *, first_reviews=None):
    state = review.prepare(question, documents)
    # Include comparison inputs in cache key without exposing them to the model.
    comparison = first_reviews or {}
    ident = review.question_hash(dict(input_id=review.identity(state), first_reviews=comparison))
    folder = Path(root)/ident
    with file_lock(folder/'job.lock'):
        if (folder/'job.json').exists():
            return ident
        save(folder/'input.json', dict(state=state, first_reviews=comparison))
        save(folder/'job.json', dict(id=ident, input_sha256=review.sha((folder/'input.json').read_text(encoding='utf-8')),
            input_id=review.identity(state), status='queued', attempts=0, created_at=now()))
    return ident


def enqueue_bundle(root, source):
    """Saved source bundle adapter: bounded batches, explicit oversize omissions."""
    jobs = []; skipped = []; batch = []; comparisons = {}
    q = source['request']
    def flush():
        if batch:
            jobs.append(enqueue(root, q, batch, first_reviews=comparisons))
            batch.clear(); comparisons.clear()
    for n, (url, page) in enumerate(sorted(source['pages'].items())):
        sid = 'source-'+str(n+1)
        meta = page.get('capture_metadata') or {}
        doc = dict(id=sid, url=url, text=page['content'], body_sha256=page['content_sha256'],
            complete=meta.get('evidence_kind')=='full_text' and not bool(meta.get('truncated')),
            retrieved_at=page.get('retrieved_at_utc'))
        try: review.prepare(q, [doc])
        except ValueError as exc:
            if str(exc) != 'REVIEW_INPUT_LIMIT': raise
            skipped.append(dict(id=sid, url=url, code='REVIEW_INPUT_LIMIT', grade='unresolved')); continue
        if len(batch) == review.MAX_DOCUMENTS: flush()
        try: review.prepare(q, batch+[doc])
        except ValueError as exc:
            if str(exc) != 'REVIEW_INPUT_LIMIT': raise
            flush()
        batch.append(doc)
        comparisons[sid] = source.get('source_bindings', {}).get(url, {})
    flush()
    result = dict(schema='independent-review-enqueue/1', jobs=jobs, skipped=skipped,
        source_snapshot_id=source.get('source_snapshot_id'), context_sha256=source.get('context_sha256'),
        reviewed=False, provider_calls=0, source_mutations=0)
    save(Path(root)/('bundle-'+review.question_hash(result)+'.json'), result)
    return result


def enqueue_context(root, value):
    """Use the sealed snapshot, including pending bodies, without prior interpretation in the prompt."""
    from .products import verify_context
    snapshot = verify_context(value)
    pages = {}; bindings = {}
    for material in snapshot['materials']:
        source=material['source'];body=material.get('text');url=source['source_url']
        if source.get('status')!='extracted' or not body:continue
        if url in pages and pages[url]['content']!=body:raise ValueError('AMBIGUOUS_SOURCE_BODY')
        pages[url]=dict(content=body,content_sha256=source['text_sha256'],capture_metadata=source,retrieved_at_utc=source.get('captured_at'))
        bindings[url]=material['binding']
    return enqueue_bundle(root,dict(request=value['request'],pages=pages,source_bindings=bindings,
        source_snapshot_id=value['snapshot_id'],context_sha256=review.question_hash(value)))


def optional_enqueue(value):
    """Fast local opt-in handoff only; no provider or cloud call on the capture path."""
    import os
    destination=os.environ.get('METACULUS_INDEPENDENT_REVIEW_QUEUE')
    if not destination:return None
    if not Path(destination).is_absolute():raise ValueError('REVIEW_QUEUE_ABSOLUTE_PATH_REQUIRED')
    return enqueue_context(destination,value)


def run_one(root, ident, *, router=None, retry_failed=False):
    if not re.fullmatch('[a-f0-9]{64}', ident): raise ValueError('REVIEW_JOB_ID')
    folder = Path(root)/ident
    with file_lock(folder/'job.lock', timeout=0):
        job = load(folder/'job.json'); inp = load(folder/'input.json')
        if review.sha((folder/'input.json').read_text(encoding='utf-8')) != job['input_sha256']:
            raise ValueError('REVIEW_JOB_INPUT_CHANGED')
        state = inp['state']
        if review.identity(state) != job['input_id']:
            raise ValueError('REVIEW_JOB_POLICY_CHANGED')
        if job['status'] == 'reviewed':
            # Revalidate cached raw judgments, binding and grading with exact current inputs.
            report = review.assemble(state, load(folder/'model-output.json'), first_reviews=inp['first_reviews'])
            if report != load(folder/'report.json'):
                raise ValueError('REVIEW_CACHE_CHANGED')
            if review.sha_bytes((folder/'review.parquet').read_bytes()) != job['parquet_sha256']:
                raise ValueError('REVIEW_CACHE_CHANGED')
            return job
        if job['status'] == 'running':
            # No automatic duplicate model call after a crash of unknown outcome.
            job.update(status='interrupted', error='REVIEW_ATTEMPT_INTERRUPTED', updated_at=now())
            save(folder/'job.json', job); return job
        if job['status'] != 'queued' and not (retry_failed and job['status'] in {'failed', 'interrupted'} and job['attempts'] < 2):
            return job
        if router is None:
            from pipeline_v2.routing import configure_credentials
            from pipeline_v2.providers import Budget, Transport, Router
            configure_credentials()
            router = Router(Transport(Budget(Path(root)/'budgets'), 'review-'+ident[:24],
                deadline=time.monotonic()+200), key_names=('OPENROUTER_API_KEY','OPENROUTER_API_KEY2'))
        job.update(status='running', attempts=job['attempts']+1, updated_at=now())
        save(folder/'job.json', job)
        attempt = folder/('attempt-'+str(job['attempts']))
        save(attempt/'input.json', state)
        try:
            result = router.chat(review.SYSTEM, state, model=review.MODEL, max_tokens=4200,
                                 json_mode=False, transient_attempts=2)
            save(attempt/'model-output.json', result)
            report = review.assemble(state, result, first_reviews=inp['first_reviews'])
            save(folder/'model-output.json', result); save(folder/'report.json', report)
            from .products import product
            raw = product('quality_review', report); atomic_bytes(folder/'review.parquet', raw)
            job.update(status='reviewed', parquet_sha256=review.sha_bytes(raw), updated_at=now())
            job.pop('error', None)
        except Exception as exc:
            code = str(getattr(exc, 'code', str(exc)))
            job.update(status='failed', error=code if re.fullmatch('[A-Z_0-9]{1,100}', code) else type(exc).__name__, updated_at=now())
            save(attempt/'failure.json', dict(error=job['error'], all_sources_retained=True))
        transport = getattr(router, 'transport', None)
        save(attempt/'receipt.json', dict(usage=transport.actual_usage() if transport else None,
            events=getattr(router, 'retry_receipts', []), status=job['status']))
        save(folder/'job.json', job)
        return job


def main():
    p=argparse.ArgumentParser(); p.add_argument('--root',required=True)
    sub=p.add_subparsers(dest='command',required=True)
    en=sub.add_parser('enqueue'); en.add_argument('--bundle', required=True)
    run=sub.add_parser('run-one'); run.add_argument('--id',required=True); run.add_argument('--retry-failed',action='store_true')
    args=p.parse_args()
    result=enqueue_bundle(args.root,load(args.bundle)) if args.command=='enqueue' else run_one(args.root,args.id,retry_failed=args.retry_failed)
    print(json.dumps(result))


if __name__ == '__main__': main()
