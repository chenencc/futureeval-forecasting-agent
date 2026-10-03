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
    from ForecastAgent.readers.capture_status import capture_status
    return capture_status(attempt, capture)


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
            inspection=bundle.get('program_inspection')
            if inspection:
                if inspection.get('schema')!='collection-inspection-v1':
                    raise ValueError('Unknown collection inspection schema')
                for url,check in inspection['pages'].items():
                    capture=captures.get(url,{})
                    body=capture.get('content','')
                    if hashlib.sha256(body.encode()).hexdigest()!=check['body_sha256']:
                        raise ValueError('Collection inspection body changed')
                    # Successful HTTP can still yield a navigation or login shell.
                    # This is a quality gap, not a fictitious failed physical request.
                    if url in seen or check['diagnostics']['body']['usable_text']:continue
                    seen.add(url)
                    row={'task_id':task_id,'task_state':task.get('status'),'url':url,
                        'domain':urlsplit(url).hostname,'kind':'body_quality_gap',
                        'attempted_at':capture.get('retrieved_at_utc'),
                        **classify({},capture)}
                    rows.append(row)
            # HTTP-success shells and parsing failures are independent quality gaps.
            for url,capture in captures.items():
                if url in seen:continue
                diagnostic=classify({},capture)
                if diagnostic['category']=='already_recovered':continue
                seen.add(url)
                rows.append({'task_id':task_id,'task_state':task.get('status'),'url':url,
                    'domain':urlsplit(url).hostname,'kind':'body_quality_gap',
                    'attempted_at':capture.get('retrieved_at_utc'),**diagnostic})
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
        'unique_failed_task_url_pairs':sum(r['kind']=='failed_fetch' for r in rows),
        'body_quality_gap_count':sum(r['kind']=='body_quality_gap' for r in rows),
        'unique_repair_candidate_task_url_pairs':len(rows),
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
