"""Build an offline repair inventory without reopening acquisition tasks.

Plans are proposals, not execution grants. Original URLs, task states, provider
budgets and snapshots remain immutable. Browser rendering is not an access grant.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


def classify(attempt: dict, capture: dict | None = None) -> dict:
    capture = capture or {}
    detail = str(attempt.get('detail') or attempt.get('error') or '')
    diagnostic = (capture.get('body_diagnostics') or {}).get('state')
    match = re.search(r'HTTP Error (\d{3})', detail)
    status = int(match.group(1)) if match else None
    if capture.get('content') and (capture.get('body_diagnostics') or {}).get('usable_text'):
        category, route = 'already_recovered', 'reuse_existing_capture'
    elif status in {401, 403} or diagnostic in {'access_interstitial', 'login_preview'}:
        category, route = 'access_restricted', 'alternate_public_source_or_authorized_access'
    elif status == 404:
        category, route = 'missing_url', 'official_feed_sitemap_or_parent_discovery'
    elif status == 429:
        category, route = 'rate_limited', 'respect_retry_deadline'
    elif status is not None and status >= 500:
        category, route = 'service_failure', 'bounded_later_retry'
    elif 'exceeds size limit' in detail.lower():
        category, route = 'size_limit', 'bounded_streaming_download'
    elif 'Unsupported content type' in detail or capture.get('parse_failure'):
        category, route = 'format_or_parser_gap', 'reparse_saved_bytes_or_add_format_reader'
    elif diagnostic == 'javascript_shell':
        category, route = 'javascript_shell', 'bounded_browser_render'
    elif diagnostic in {'navigation_shell', 'government_banner_only'}:
        category, route = 'index_or_banner', 'discover_detail_or_structured_endpoint'
    elif 'codec' in detail.lower() or diagnostic == 'corrupt_text':
        category, route = 'encoding_gap', 'repair_encoding'
    elif 'empty page' in detail.lower() or diagnostic == 'empty_text':
        category, route = 'empty_or_interstitial_unknown', 'inspect_saved_response_then_route'
    else:
        category, route = 'transport_or_unknown', 'inspect_transport_then_bounded_retry'
    return {'category':category, 'proposed_route':route, 'http_status':status,
        'body_state':diagnostic, 'original_detail':detail,
        'saved_raw_available':bool(capture.get('raw_response_base64')),
        'execution_authorized':False}


def inventory(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as source:
        campaign = json.loads(source.read('campaign.json'))
        rows, gaps, all_failed = [], [], 0
        for name in sorted(source.namelist()):
            if not name.endswith('/bundle.json'):
                continue
            bundle = json.loads(source.read(name))
            task_id = name.split('/')[-2]
            task = campaign['tasks'].get(task_id, {})
            captures = {r.get('url'):r.get('page') or {} for r in bundle.get('failed_captures', [])}
            captures.update(bundle.get('pages', {}))
            seen = set()
            for attempt in bundle.get('fetch_attempts', []):
                if attempt.get('status') != 'failed':
                    continue
                all_failed += 1
                url = attempt.get('url','')
                if url in seen:
                    continue
                seen.add(url)
                # Match exact URLs first. Do not erase query strings carrying data IDs.
                capture = captures.get(url, {})
                row = {'task_id':task_id, 'task_state':task.get('status'), 'url':url,
                    'domain':urlsplit(url).hostname, 'kind':'failed_fetch',
                    'attempted_at':attempt.get('at'), **classify(attempt,capture)}
                rows.append(row)
            quality = task.get('quality_inventory') or {}
            gaps.append({'task_id':task_id, 'task_state':task.get('status'),
                'raw_capture_count':quality.get('raw_capture_count'),
                'parse_gap_urls':quality.get('parse_gap_urls', []),
                'unmatched_candidate_urls':quality.get('unmatched_candidate_urls', []),
                'possible_index_shell_count':quality.get('possible_index_shell_count', 0),
                'termination_reason':quality.get('termination_reason'),
                'reported_gaps':task.get('gaps', []),
                'existing_resource_counts':task.get('resources', {})})
    with archive.open('rb') as archive_file:
        parent_hash = hashlib.file_digest(archive_file,'sha256').hexdigest()
    return {'schema':'acquisition_repair_inventory_v1',
        'parent_archive':str(archive.resolve()),
        'parent_sha256':parent_hash,
        'parent_input_sha256':campaign.get('input_sha256'),
        'state_distribution':dict(Counter(t.get('status') for t in campaign['tasks'].values())),
        'failed_physical_fetch_attempts':all_failed,
        'unique_failed_task_url_pairs':len(rows),
        'failure_categories':dict(Counter(r['category'] for r in rows)),
        'failed_domains':dict(Counter(r['domain'] for r in rows).most_common()),
        'proposed_routes':dict(Counter(r['proposed_route'] for r in rows)),
        'failed_fetches':rows, 'task_gaps':gaps,
        'policy':{'planning_only':True, 'network_calls':0, 'model_calls':0,
            'search_calls':0, 'original_tasks_reopened':False, 'budget_reset':False,
            'new_supplement_budget_required':True,
            'new_capture_requires_child_lineage':True,
            'page_readability_does_not_establish_relevance':True},
        'limitations':['HTTP status alone does not identify JavaScript rendering needs.',
            'Unattempted leads and reported semantic gaps are separate from failed HTTP attempts.',
            'Browser rendering cannot grant access to private forecasts or historical content.',
            'Routes are proposals; this inventory contains no crawler or repair executor.']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=inventory(args.archive)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['failed_physical_fetch_attempts','unique_failed_task_url_pairs','failure_categories','proposed_routes']},indent=2))


if __name__=='__main__':
    main()
