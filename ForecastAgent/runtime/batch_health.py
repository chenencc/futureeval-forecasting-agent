"""Batch failure taxonomy and evidence-backed accounting without provider calls."""
from collections import Counter
from datetime import datetime
import hashlib
from pathlib import Path
import json
import re


def failure(error=None, bundle=None, records=()):
    bundle=bundle or {}
    statuses=[r.get('http_status') for r in records]
    detail=str(error or bundle.get('last_error_detail') or '').lower()
    if any(s in {401,402,403} for s in statuses):
        return {'category':'provider_credentials_or_quota','retryable':False,'pause_batch':True}
    if 429 in statuses or 'rate limit' in detail:
        return {'category':'provider_rate_limit','retryable':True,'pause_batch':False}
    if any(isinstance(s,int) and s>=500 for s in statuses):
        return {'category':'provider_unavailable','retryable':True,'pause_batch':False}
    if isinstance(error,(ValueError,TypeError,KeyError)) or 'hash mismatch' in detail:
        return {'category':'contract_or_integrity','retryable':False,'pause_batch':False}
    if any(s.get('status')=='reserved' for s in bundle.get('model_attempts',[])):
        return {'category':'interrupted_transport_unknown','retryable':True,'pause_batch':False}
    if any(r.get('status')=='missing_choices' for r in records):
        return {'category':'model_response_unavailable','retryable':True,'pause_batch':False}
    if any(w in detail for w in ('timeout','timed out','deadline','temporary','connection','transport failure')):
        return {'category':'transient_execution','retryable':True,'pause_batch':False}
    if (bundle.get('result') or {}).get('termination_reason') in {'deadline','model_dispatch_budget','program_dispatch_limit'}:
        return {'category':'bounded_dispatch_interruption','retryable':True,'pause_batch':False}
    return {'category':'unclassified_execution','retryable':False,'pause_batch':False}


def discovery_failures(bundle,before):
    records=[]
    for name in ('searches','exa_searches'):
        for row in bundle.get(name,[])[before.get(name,0):]:
            if row.get('status')!='failed':continue
            detail=str(row.get('detail') or row.get('error_detail') or row.get('error') or '')
            code=row.get('http_status')
            if not code:
                match=re.search(r'HTTP(?: Error)?[ :]+(\d{3})',detail,re.I)
                code=int(match[1]) if match else None
            records.append({'http_status':code,'status':'discovery_failed','provider':name})
    return records


def transport_records(directory, attempts):
    root=Path(directory).resolve();records=[]
    for attempt in attempts:
        if not attempt.get('path'):continue
        path=(root/attempt['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Model transport path escapes task directory')
        if not path.exists():
            raise ValueError('Model transport record missing')
        raw=path.read_bytes()
        if attempt.get('sha256') and hashlib.sha256(raw).hexdigest()!=attempt['sha256']:
            raise ValueError('Model transport hash mismatch')
        records.append(json.loads(raw))
    return records


def retry_minutes(attempts):
    return min(360,30*2**min(max(attempts-1,0),4))


def task_summary(bundle):
    from ForecastAgent.evidence.raw_capture import capture_report
    report=capture_report(bundle)
    seconds=0
    for session in bundle.get('sessions',[]):
        if session.get('started_at') and session.get('finished_at'):
            seconds+=max(0,(datetime.fromisoformat(session['finished_at'])-datetime.fromisoformat(session['started_at'])).total_seconds())
    return {'raw_capture_count':report['capture_count'],'possible_index_shell_count':report['possible_index_shell_count'],
        'raw_integrity_passed':report['raw_integrity_passed'],'parse_gap_urls':report['parse_gap_urls'],
        'unmatched_candidate_urls':report['unmatched_candidate_urls'],
        'failed_source_count':len(report['failed_source_attempts']),
        'failed_search_count':sum(r.get('status')=='failed' for key in ('searches','exa_searches') for r in bundle.get(key,[])),
        'source_http':len(bundle.get('fetch_attempts',[])),'extract_batches':len(bundle.get('extract_attempts',[])),
        'termination_reason':(bundle.get('result') or {}).get('termination_reason'),
        'elapsed_session_seconds':seconds,'full_recall_verified':False}


def completion_state(entry):
    return {'pending':'pending','running':'running','acquired':'complete','closed_with_gaps':'complete_with_gaps',
        'incomplete':'retryable_interruption','blocked_budget':'budget_exhausted',
        'needs_attention':'terminal_failure'}.get(entry['status'],'terminal_failure')
