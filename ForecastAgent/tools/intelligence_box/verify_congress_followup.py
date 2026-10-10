"""Explicit action-page and public-law follow-up using saved parent captures."""
import json
from pathlib import Path
from .core import Toolbox,now


def main():
    root=Path('.tmp/toolbox-priority-pilot-20261010')
    previous=json.loads((root/'congress-chain-report.json').read_text(encoding='utf-8'))
    ids={row['source']:row['capture_id'] for row in previous['cases']}
    box=Toolbox(root,max_requests=25,max_document_bytes=16_000_000)
    before=box.budget()['reserved_attempts']
    report={'checked_at_utc':now(),'search_requests':0,'model_requests':0,'cases':[],
            'pagination_is_explicit':True,'snapshot_consistency_across_pages_verified':False}
    try:
        for offset in (20,40):
            result=box.fetch('congress_actions',{'congress':119,'bill_type':'hr','bill_number':1,'limit':20,'offset':offset})
            report['cases'].append({'source':result['source_id'],'offset':offset,'capture_id':result['id'],
                                    'status':result['status'],'record_count':len(result['records']),
                                    'coverage':{k:v for k,v in result.get('coverage',{}).items() if k!='native_metadata'}})
        index=box._saved(ids['congress_texts'])[0]
        version_index=next(i for i,v in enumerate(index['records']) if v.get('type')=='Public Law')
        format_index=next(i for i,f in enumerate(index['records'][version_index]['formats']) if f.get('type')=='Formatted Text')
        result=box.bill_text(ids['congress_texts'],version_index,format_index,detail_capture_id=ids['congress_bill'])
        report['cases'].append({'source':result['source_id'],'capture_id':result['id'],'status':result['status'],
                                'http_status':result.get('http',{}).get('status'),'raw_bytes':result.get('raw_bytes'),
                                'source_binding':result.get('source_binding'),'quality':result['quality'],
                                'coverage':{k:v for k,v in result.get('coverage',{}).items() if k!='native_metadata'}})
        report['budget']=box.budget()
        report['new_physical_attempts']=box.budget()['reserved_attempts']-before
        (root/'congress-followup-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'cases':[{'source':r['source'],'status':r['status'],'record_count':r.get('record_count'),'http_status':r.get('http_status')} for r in report['cases']], 'budget':report['budget']}))
    finally:
        box.close()


if __name__=='__main__': main()
