"""Bounded public document probes for a release candidate, never forecasting."""
import argparse
import hashlib
import json
from pathlib import Path

from ForecastAgent.acquisition.pipeline import verify_baseline
from ForecastAgent.releases.manifest import verify
from ForecastAgent.supplement.stage import fetch_document, save


def run(urls, output, minimum_readable=2):
    if not isinstance(urls,list) or not 1 <= len(urls) <= 3 or len(set(urls))!=len(urls):
        raise ValueError('Select one to three unique observed public source URLs')
    verify();verify_baseline()
    output=Path(output)
    if (output/'report.json').exists():
        raise ValueError('Use a fresh probe directory; do not silently repeat requests')
    report={'schema':'release-public-document-gate-v1','sources':[],
            'model_calls':0,'tavily_calls':0,'exa_calls':0,'forecast_submissions':0,
            'production_tasks_reopened':False,'production_budget_resets':0,
            'scope':'Current public capture capability only; not forecast accuracy or original evidence.'}
    for index,url in enumerate(urls,1):
        row={'url':url,'status':'reserved'};report['sources'].append(row)
        save(output/'report.json',report)
        try:
            page=fetch_document(url)
            relative=f'captures/{index}.json';save(output/relative,page)
            diagnostic=page['body_diagnostics']
            row.update(status='readable' if diagnostic['usable_text'] else 'unreadable',
                body_diagnostics=diagnostic,parsed_chars=len(page.get('content','')),
                source_sha256=page.get('sha256'),capture_file=relative,
                capture_file_sha256=hashlib.sha256((output/relative).read_bytes()).hexdigest())
        except Exception as exc:
            row.update(status='failed',error=type(exc).__name__,detail=str(exc)[:300])
        save(output/'report.json',report)
    report['readable_sources']=sum(r['status']=='readable' for r in report['sources'])
    report['passed']=report['readable_sources']>=minimum_readable
    save(output/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--urls-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--minimum-readable',type=int,default=2)
    parser.add_argument('--fetch',action='store_true',help='Authorize these bounded public GETs')
    args=parser.parse_args()
    if not args.fetch:parser.error('Explicit --fetch is required')
    report=run(json.loads(args.urls_file.read_text()),args.output,args.minimum_readable)
    print(json.dumps({k:report[k] for k in ['passed','readable_sources','model_calls','tavily_calls','exa_calls','forecast_submissions']}))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
