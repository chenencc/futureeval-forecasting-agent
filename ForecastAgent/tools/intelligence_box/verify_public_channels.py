"""Small public pilot; preserves failures and never retries implicitly."""
import argparse
import hashlib
import json
from pathlib import Path
from .core import Toolbox

CASES=[('gdelt_news',{'query':'"Tesla"','maxrecords':5,'timespan':'1week'}),
       ('dbnomics_series',{'provider':'INSEE','dataset':'IPC-2015','series':'A.IPC.SO.00.00.INDICE.ENSEMBLE.FE.SO.BRUT.2015.FALSE'}),
       ('bls_series',{'series':'CUUR0000SA0'}),
       ('govinfo_feed',{'collection':'PLAW'}),
       ('govinfo_text',{'package':'PLAW-119publ21'})]

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',required=True); args=parser.parse_args()
    root=Path(args.root)
    box=Toolbox(root,max_requests=6)
    rows=[]
    try:
        for name,params in CASES:
            c=box.fetch(name,params)
            rows.append(c)
            print(json.dumps({'source':name,'status':c['status'],'records':len(c['records']),'http':c.get('http',{}).get('status'),'error':c.get('error')},ensure_ascii=True),flush=True)
        c=box.discover('https://www.govinfo.gov/rss/plaw.xml','rss',limit=5)
        rows.append(c)
        report={'version':'public_channels_pilot_v1','cases':[{'source_id':c['source_id'],'id':c['id'],'status':c['status'],'http_status':c.get('http',{}).get('status'),'records':len(c['records']),'raw_bytes':c.get('raw_bytes'),'raw_sha256':c.get('raw_sha256'),'hash_verified':hashlib.sha256((root/c['raw_path']).read_bytes()).hexdigest()==c['raw_sha256'] if c.get('raw_path') else None,'error':c.get('error'),'source_binding':c.get('source_binding'),'sample_records':c['records'][:2]} for c in rows], 'budget':box.budget(),'model_calls':0,'paid_search_calls':0,'production_integrated':False}
        (root/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'statuses':[(r['source_id'],r['status']) for r in rows],'budget':box.budget()}),flush=True)
    finally: box.close()
if __name__=='__main__': main()
