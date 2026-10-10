"""Bounded public discovery pilot and explicit original downloads."""
import argparse
import json
from pathlib import Path
from .core import Toolbox

CASES=[
    ('Microsoft IR','https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast','ir','microsoft_ir'),
    ('Federal Reserve RSS','https://www.federalreserve.gov/feeds/press_all.xml','rss',None),
    ('FEC sitemap','https://www.fec.gov/sitemap-wagtail.xml/','sitemap',None),
]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--capture-id')
    parser.add_argument('--index',type=int)
    args=parser.parse_args()
    root=Path(args.root)
    box=Toolbox(root,max_requests=10,max_document_bytes=16_000_000)
    try:
        if args.capture_id:
            result=box.acquire_link(args.capture_id,args.index)
            print(json.dumps({'id':result['id'],'status':result['status'],'url':result['request_url'],
                              'raw_bytes':result.get('raw_bytes'),'records':len(result['records']),
                              'error':result.get('error'),'budget':box.budget()}))
        else:
            results=[]
            for label,url,kind,profile in CASES:
                c=box.discover(url,kind,profile_id=profile,limit=40)
                results.append({'label':label,'id':c['id'],'status':c['status'],'records':c['records'],
                                'coverage':c.get('coverage'),'error':c.get('error')})
            report={'cases':results,'budget':box.budget(),'models_called':False,'search_providers_called':False}
            (root/'discovery-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report))
    finally: box.close()


if __name__=='__main__': main()
