"""Seven explicit public HTTP attempts, immutable cap, no model or search usage."""
import argparse
import hashlib
import json
from pathlib import Path
from .core import Toolbox
CASES=[('eurostat_data',{'dataset':'nama_10_gdp','filters':'{"geo":"DE","time":"2025","unit":"CP_MEUR","na_item":"B1GQ","freq":"A"}'}),('ecb_series',{'flow':'EXR','series':'M.USD.EUR.SP00.A','lastNObservations':2}),('nws_point',{'latitude':38.8894,'longitude':-77.0352}),('nws_forecast',{'office':'LWX','x':97,'y':71}),('nws_observation',{'station':'KDCA'}),('nws_alerts',{'area':'CA'}),('sec_companyfacts',{'cik':'0001318605'})]
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root)
    box=Toolbox(root,max_requests=7);cases=[]
    try:
        for source,params in CASES:
            c=box.fetch(source,params)
            entry={k:c.get(k) for k in ('id','source_id','status','raw_bytes','raw_sha256','error','budget')}
            entry.update(http_status=c.get('http',{}).get('status'),records=len(c['records']),sample_records=c['records'][:1],hash_verified=hashlib.sha256((root/c['raw_path']).read_bytes()).hexdigest()==c['raw_sha256'] if c.get('raw_path') else None)
            cases.append(entry);print(json.dumps({k:entry[k] for k in ('source_id','status','http_status','records','error')}),flush=True)
        report=dict(version='official_data_pilot_v1',cases=cases,budget=box.budget(),model_calls=0,paid_search_calls=0,production_integrated=False)
        (root/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    finally:box.close()
if __name__=='__main__':main()
