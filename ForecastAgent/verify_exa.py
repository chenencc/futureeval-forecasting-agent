"""A resumable, single-search Exa smoke test without Ultra or Tavily calls."""
import argparse
import json
from pathlib import Path

from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.task_lock import task_lock


def verify(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    request = {'id':'exa-smoke','question':'Find NOAA OISST documentation',
               'resolution_criteria':'Acquire NOAA documentation links and one readable original body.',
               'mode':'live','pipeline':'collection','acquisition_profile':'collection_v3'}
    with task_lock(root):
        task = RetrievalTask(root, request)
        task.bundle['plan'] = [{'id':'oisst','priority':'critical','condition':'NOAA OISST methodology',
                              'expected_source':'NOAA','query':'NOAA OISST documentation'}]
        task.save()
        if not task.bundle.get('result') and not task.bundle['exa_searches']:
            try:
                task.execute('search_exa',{'query':'NOAA OISST daily sea surface temperature documentation',
                    'need_ids':['oisst'],'reason':'Validate the optional Exa discovery channel',
                    'search_role':'official_gap','category':'general','include_domains':['noaa.gov']},'')
            except Exception as exc:
                task.bundle['smoke_error'] = type(exc).__name__
                task.save()
        hits = [hit for attempt in task.bundle['exa_searches'] for hit in attempt.get('results',[])]
        if not task.bundle.get('result') and hits and not task.bundle['fetch_attempts']:
            try:
                task.execute('fetch_page',{'url':hits[0]['url']},'')
            except Exception as exc:
                task.bundle['smoke_read_error'] = type(exc).__name__
                task.save()
        gaps = [] if task.bundle['pages'] else ['No readable source body captured in the one-attempt smoke test.']
        if not task.bundle.get('result'):
            task.execute('finish_collection',{'gaps':gaps},'')
        task.save()
        attempts = task.bundle['exa_searches']
        report = {'schema':'exa_smoke_v1','success':bool(hits),'search_attempts':len(attempts),
            'search_status':attempts[0]['status'] if attempts else 'disabled',
            'http_status':attempts[0].get('http_status') if attempts else None,
            'request_id':attempts[0].get('request_id') if attempts else None,
            'cost_dollars_estimate':attempts[0].get('cost_dollars_estimate') if attempts else None,
            'urls':[h['url'] for h in hits], 'saved_bodies':len(task.bundle['pages']),
            'body_chars':{url:len(page['content']) for url,page in task.bundle['pages'].items()},
            'tavily_attempts':len(task.bundle['searches']),'ultra_attempts':len(task.bundle.get('model_attempts',[])),
            'forecast_submissions':False, 'remaining_budget':task.budget(),
            'billing_note':'Provider cost estimate, not remaining monthly account credit.'}
        (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
        return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='snapshots/exa-verification')
    arguments=parser.parse_args()
    if not verify(arguments.root)['success']:
        raise SystemExit(1)
