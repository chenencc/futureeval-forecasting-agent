"""Inspect imported campaign evidence without trusting summary counters."""
import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3

from ForecastAgent.runtime.collection_v2 import eligible, late_dates
from ForecastAgent.readers.saved import select, version_digest


def review(root, artifact):
    db = sqlite3.connect(f'file:{(root / "index.sqlite3").as_posix()}?mode=ro', uri=True)
    files = dict(db.execute('SELECT path,sha256 FROM files WHERE artifact_id=?', (artifact,)))
    rows = db.execute("SELECT path,body_json FROM records WHERE artifact_id=? AND kind='json_document' AND path LIKE '%/bundle.json'", (artifact,)).fetchall()
    results = []
    for path, encoded in rows:
        bundle = json.loads(encoded)
        cutoff = datetime.fromisoformat(bundle['request']['as_of_utc'].replace('Z', '+00:00'))
        attempts = bundle.get('model_attempts', [])
        transports = []
        for attempt in attempts:
            relative = str(PurePosixPath(path).parent / attempt.get('path', 'MISSING'))
            digest = files.get(relative)
            record = {}
            hash_matches = False
            if digest:
                raw = (root / 'blobs' / 'sha256' / digest[:2] / digest).read_bytes()
                hash_matches = hashlib.sha256(raw).hexdigest() == digest == attempt.get('sha256')
                record = json.loads(raw)
            response = record.get('response') or {}
            usage = response.get('usage') or {}
            transports.append({'id': attempt.get('id'), 'path': relative,'raw_sha256':digest,
                'record_present': bool(record), 'sha256_verified': hash_matches,
                'status': record.get('status', attempt.get('status')),
                'model': (record.get('request') or {}).get('model'),
                'http_status': record.get('http_status'), 'usage': usage,
                'error': record.get('error'), 'duration_seconds': record.get('duration_seconds'),
                'request_chars': len(json.dumps(record.get('request') or {})),
                'response_preview': record.get('response_body', '')[:250] if record.get('status') != 'received' else None})
        pages = []
        for url, page in bundle.get('pages', {}).items():
            body = page.get('content', '')
            docs = page.get('documents', [])
            try:
                raw_verified = hashlib.sha256(base64.b64decode(page['raw_response_base64'], validate=True)).hexdigest() == page['sha256']
            except (KeyError, ValueError, TypeError):
                raw_verified = False
            pages.append({'url': url, 'chars': len(body), 'documents': len(docs),
                'eligible_for_model_under_v3': eligible(page, cutoff),
                'temporal_status': page.get('temporal_status'),
                'capture_method': page.get('capture_method'),
                'archive_timestamp': page.get('archive_timestamp'),
                'retrieved_at_utc': page.get('retrieved_at_utc'),
                'published_at': page.get('published_at'),
                'updated_at': page.get('updated_at'),
                'content_hash_matches': hashlib.sha256(body.encode()).hexdigest() == page.get('content_sha256'),
                'raw_hash_matches': raw_verified,
                'content_truncated': page.get('content_truncated'),
                'documents_truncated': page.get('documents_truncated'),
                'diagnostics': page.get('body_diagnostics'),
                'dataset': page.get('dataset'), 'unit': page.get('unit'),
                'data_warning': page.get('data_warning'), 'dated_request': page.get('dated_request'),
                'pagination': page.get('pagination'), 'requested_range': page.get('requested_range'),
                'row_count': len(page.get('rows', [])),
                'row_head': page.get('rows', [])[:3], 'row_tail': page.get('rows', [])[-3:],
                'table_count': sum('table' in str(doc.get('metadata', {}).get('format', '')) for doc in docs),
                'late_date_mentions': late_dates(body, cutoff)[:20],
                'preview': body[:1200], 'tail': body[-600:]})
        excerpts = []
        for item in bundle.get('excerpts', []):
            try:
                versions = [bundle['pages'].get(item['url'], {})] + bundle.get('page_history', {}).get(item['url'], [])
                source = next(p for p in versions if p.get('sha256') == item.get('source_sha256')
                              and version_digest(p) == item.get('source_parsed_sha256'))
                page, body, _ = select({item['url']:source}, item['url'], item.get('location', {}).get('document_index'))
                coordinate_matches = body[item['start_char']:item['end_char']] == item['text']
                version_matches = version_digest(page) == item.get('source_parsed_sha256')
                eligible_body = eligible(page, cutoff)
            except (ValueError, KeyError, TypeError, StopIteration):
                coordinate_matches = version_matches = eligible_body = False
            excerpts.append({**item, 'audit_coordinate_matches': coordinate_matches,
                'audit_version_matches': version_matches, 'audit_v3_body_eligible': eligible_body,
                'late_date_mentions': late_dates(item.get('text', ''), cutoff)})
        searches = [{key: search.get(key) for key in ('query','search_role','status','depth','start_date','end_date','search_options','need_ids','reason')} | {
            'result_count': len(search.get('results', [])),
            'results': [{key: result.get(key) for key in ('url','published_date','title')} for result in search.get('results', [])]
            } for search in bundle.get('searches', [])]
        tool_errors = [step for step in bundle.get('transcript', []) if step.get('error') or (isinstance(step.get('result'), dict) and step['result'].get('error'))]
        results.append({'question_id': str(bundle['request']['id']), 'request': bundle['request'],
            'frozen_request_hash':bundle['request_hash'],
            'bundle_path': path, 'result': bundle.get('result'), 'acceptance': bundle.get('acceptance'),
            'limits': bundle.get('acquisition_limits'), 'resources': bundle.get('resources'),
            'model_http_attempts': len(attempts), 'transport_records_verified': sum(x['sha256_verified'] for x in transports),
            'model_statuses': dict(Counter(x['status'] for x in transports)),
            'known_total_tokens': sum(x['usage'].get('total_tokens', 0) for x in transports),
            'known_prompt_tokens': sum(x['usage'].get('prompt_tokens', 0) for x in transports),
            'known_completion_tokens': sum(x['usage'].get('completion_tokens', 0) for x in transports),
            'attempts_without_usage': sum(not x['usage'] for x in transports),
            'transport_records': transports, 'search_attempts': len(searches), 'searches': searches,
            'search_payload_sha256':[hashlib.sha256(json.dumps(s,sort_keys=True).encode()).hexdigest() for s in bundle.get('searches',[])],
            'exa_search_attempts':len(bundle.get('exa_searches',[])), 'exa_searches':bundle.get('exa_searches',[]),
            'exa_reported_cost':sum((s.get('cost_dollars_estimate') or {}).get('total',0) for s in bundle.get('exa_searches',[])),
            'budget_amendments':bundle.get('budget_amendments',[]),
            'page_count': len(pages), 'v3_eligible_page_count': sum(p['eligible_for_model_under_v3'] for p in pages),
            'pages': pages, 'excerpt_count': len(excerpts), 'excerpts': excerpts,
            'tool_counts': dict(Counter(s.get('tool', 'unknown') for s in bundle.get('transcript', []))),
            'tool_errors': tool_errors, 'plan': bundle.get('plan'),
            'channel_decisions': bundle.get('channel_decisions'),
            'context_projections': bundle.get('context_projections'),
            'failed_captures': bundle.get('failed_captures'),
            'fetch_attempts': bundle.get('fetch_attempts'),
            'extract_attempts': bundle.get('extract_attempts'),
            'quarantine': bundle.get('quarantine')})
    db.close()
    return results


KEYS=('model_http_attempts','known_total_tokens','known_prompt_tokens','known_completion_tokens',
      'search_attempts','exa_search_attempts','exa_reported_cost','page_count','v3_eligible_page_count',
      'excerpt_count','transport_records_verified','attempts_without_usage')


def compare(old, prior, current):
    """Retain prefix costs; distinguish new work from total task expenditure."""
    old_by_id={row['question_id']:row for row in old}
    prior_by_id={row['question_id']:row for row in prior}
    comparisons=[]
    for row in current:
        previous=prior_by_id[row['question_id']]
        count=previous['model_http_attempts']
        prefix=[r['raw_sha256'] for r in row['transport_records'][:count]]
        prior_prefix=[r['raw_sha256'] for r in previous['transport_records']]
        search_count=previous['search_attempts']
        checks={'model_transport_prefix_unchanged':prefix==prior_prefix,
                'tavily_search_prefix_unchanged':row['search_payload_sha256'][:search_count]==previous['search_payload_sha256'],
                'request_hash_unchanged':row['frozen_request_hash']==previous['frozen_request_hash'],
                'tavily_total_within_frozen_budget':row['search_attempts']<=row['limits']['tavily_basic'],
                'exa_total_within_frozen_budget':row['exa_search_attempts']<=row['limits'].get('exa_search',0),
                'all_transport_files_verified':row['transport_records_verified']==row['model_http_attempts']}
        comparisons.append({'question_id':row['question_id'],'old_v2':{k:old_by_id[row['question_id']].get(k,0) for k in KEYS},
            'interrupted_v3':{k:previous.get(k,0) for k in KEYS},
            'resume_increment':{k:row.get(k,0)-previous.get(k,0) for k in KEYS},
            'cumulative':{k:row.get(k,0) for k in KEYS},'ledger_checks':checks,
            'completion':row['result'],'acceptance':row['acceptance']})
    totals={phase:{k:sum(row[phase][k] for row in comparisons) for k in KEYS}
            for phase in ('old_v2','interrupted_v3','resume_increment','cumulative')}
    return comparisons, totals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('artifact')
    parser.add_argument('--run', required=True)
    parser.add_argument('--root', default='E:/metaculus_data')
    parser.add_argument('--prior-artifact',default='11134805972')
    parser.add_argument('--baseline-artifact',default='11113955416')
    args = parser.parse_args()
    root = Path(args.root)
    baseline = review(root, args.baseline_artifact)
    prior = review(root,args.prior_artifact)
    current = review(root, args.artifact)
    comparisons,totals=compare(baseline,prior,current)
    result = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'run_id': args.run,
        'artifact_id': args.artifact, 'baseline_run_id': '36745235245',
        'prior_run_id':'36796437198','prior_artifact_id':args.prior_artifact,
        'caveats': ['Resume increments are not a fresh-run estimate: prior captures, remaining budgets and the completed BTC task are reused.',
                   'Exploratory comparison, not controlled A/B: policy and search budgets differ; search and model outputs change.',
                   'Date-bounded current-vintage observations are not proof of historical data availability.',
                   'Future date mentions are review leads, not automatic proof of outcome leakage.',
                   'Model knowledge and source criteria revisions prevent a clean historical backtest.'],
        'comparisons': comparisons, 'totals':totals,'current': current, 'prior':prior,'baseline': baseline}
    output = root / 'reports' / f'collection-resume-{args.run}.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report':str(output),'totals':totals,
        'questions':[{'id':r['question_id'],'increment':r['resume_increment'],'ledger_checks':r['ledger_checks']} for r in comparisons]}, ensure_ascii=True))


if __name__ == '__main__':
    main()
