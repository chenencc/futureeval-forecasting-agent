"""Bounded official bill-detail, action and original-text chain verification."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin
from .core import Toolbox,now


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    args=parser.parse_args()
    root=Path(args.root)
    # Reuse the existing 25-request ledger. The helper never changes its cap.
    box=Toolbox(root,max_requests=25,max_document_bytes=16_000_000)
    report={'schema':'congress_chain_validation_v1','started_at_utc':now(),
            'model_requests':0,'search_requests':0,'submitted':False,'cases':[]}
    before=box.budget()['reserved_attempts']
    params={'congress':119,'bill_type':'hr','bill_number':1}
    def save(label,result):
        row={'label':label,'capture_id':result.get('id'),'source':result['source_id'],
             'status':result['status'],'http_status':result.get('http',{}).get('status'),
             'url':result.get('request_url'),'record_count':len(result['records']),
             'raw_bytes':result.get('raw_bytes'),'source_binding':result.get('source_binding'),
             'coverage':result.get('coverage'),'quality':result.get('quality')}
        if row['coverage']:
            row['coverage']={k:v for k,v in row['coverage'].items() if k!='native_metadata'}
        if result.get('raw_path'):
            row['hash_matches']=hashlib.sha256((root/result['raw_path']).read_bytes()).hexdigest()==result['raw_sha256']
        report['cases'].append(row)
        report['budget']=box.budget()
        report['new_physical_attempts']=box.budget()['reserved_attempts']-before
        (root/'congress-chain-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({k:row[k] for k in ('label','status','http_status','record_count')}),flush=True)
        return result
    try:
        detail=save('Exact bill detail',box.fetch('congress_bill',params))
        if detail['status']=='usable':
            report['native_stage_fields']={k:detail['records'][0].get(k) for k in ('title','introducedDate','latestAction','laws','updateDate','updateDateIncludingText')}
        actions=save('Bounded action history',box.fetch('congress_actions',dict(params,limit=20)))
        texts=save('Text version index',box.fetch('congress_texts',params))
        if texts['status']=='usable':
            report['text_versions']=[{'version_index':i,'type':v.get('type'),'date':v.get('date'),'formats':v.get('formats')} for i,v in enumerate(texts['records'])]
            # Prefer a returned enrolled version for this fixture; it remains a
            # text-version label, never an inferred enactment certificate.
            versions=list(enumerate(texts['records']))
            versions.sort(key=lambda pair: 'enrolled' not in str(pair[1].get('type','')).lower())
            for index,version in versions[:1]:
                formats=list(enumerate(version.get('formats',[])))
                formats.sort(key=lambda pair: not str(pair[1].get('url','')).endswith('.htm'))
                for format_index,fmt in formats[:2]:
                    result=save('Original official text '+str(fmt.get('type')),box.bill_text(texts['id'],index,format_index))
                    if result.get('http',{}).get('status') in {301,302,303,307,308}:
                        destination=urljoin(result['request_url'],result['http'].get('redirect_location',''))
                        save('Explicit original-text redirect',box.read(destination))
                    if result['status']=='usable': break
        report['finished_at_utc']=now()
        report['native_fields_are_not_factual_verification']=True
        report['budget']=box.budget()
        report['new_physical_attempts']=box.budget()['reserved_attempts']-before
        (root/'congress-chain-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    finally:
        box.close()


if __name__=='__main__': main()
