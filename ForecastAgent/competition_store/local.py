"""Import confirmed local Market Pulse records without scanning experiments."""
import gzip
import hashlib
import json
from pathlib import Path
from ForecastAgent.market_store.store import clock,digest,encoded
from . import store

def market_pulse(db,data_root):
    root=Path(data_root)/'tournaments/market-pulse-26q4'
    if not root.is_dir():return {'status':'not_present'}
    receipts=[];results=[];packages=[];inventories=[]
    for folder in ('deliveries','campaigns'):
        for path in sorted((root/folder).rglob('submission.json')):
            value=json.loads(path.read_bytes())
            if value.get('status')=='accepted' and value.get('confirmed_at_utc') and value.get('payload'):
                receipts.append((path,value))
    if not receipts:return {'status':'no_confirmed_receipts'}
    ids={str(v['payload']['question']) for _,v in receipts}
    for folder in ('analysis','campaigns'):
        for path in sorted((root/folder).rglob('result.json')):
            if path.stat().st_size>16*1024**2:continue
            value=json.loads(path.read_bytes());payload=value.get('payload') or value.get('payload_preview')
            if isinstance(payload,dict) and str(payload.get('question')) in ids and any(payload==r['payload'] for _,r in receipts):
                results.append((path,value if value.get('payload') else {**value,'payload':payload}))
    for path in sorted((root/'campaigns').rglob('acquisition-package.json')):
        value=json.loads(path.read_bytes())
        if str(value.get('request',{}).get('id')) in ids:packages.append((path,value))
    for path in sorted((root/'snapshots').glob('*/inventory.json')):
        inventories.append((path,json.loads(path.read_bytes())))
    sources=receipts+results+packages+inventories
    source_id='local-market-pulse-v2:'+digest([(str(p.relative_to(root)),hashlib.sha256(p.read_bytes()).hexdigest()) for p,_ in sources])
    if db.execute('SELECT 1 FROM imports WHERE id=?',(source_id,)).fetchone():return {'status':'already_imported','source_id':source_id}
    at=clock()
    with db:
        for path,value in sources:
            raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
            store.save(db,'native_objects',(sha,gzip.compress(raw,mtime=0),len(raw),'gzip'))
            ident=str((value.get('payload') or value.get('request') or {}).get('question') or value.get('request',{}).get('id') or '')
            key=store.identify(db,ident) if ident.isdecimal() else None
            ref={'local_archive_relative_path':str(path.relative_to(root)),'tournament':'market-pulse-26q4','sha256':sha}
            store.save(db,'file_refs',(digest(ref),source_id,key,str(path.relative_to(root)),sha,len(raw),'verified_local_object',encoded(ref).decode()))
        inventory_by_id={}
        for _,inventory in inventories:
            observed=inventory.get('retrieved_at_utc') or at
            for row in inventory.get('rows',[]):
                if str(row['question_id']) not in ids:continue
                inventory_by_id[str(row['question_id'])]=row
                store.question(db,row,'market-pulse-26q4',observed,source_id)
        for path,value in packages:
            ident=str(value['request']['id'])
            store.bundle(db,ident,value,'local:'+str(path.relative_to(root)),at,source_id)
            # Supplemental request metadata fills options/scaling missing from
            # the old inventory without claiming a fresh platform status read.
            request={**inventory_by_id.get(ident,{}),**value['request'],
                'status':'not_refreshed','metadata_role':'archived_request'}
            store.question(db,request,'market-pulse-26q4',at,source_id)
        for _,value in results:store.candidate(db,str(value['payload']['question']),value,at,source_id)
        for _,value in sorted(receipts,key=lambda p:p[1]['confirmed_at_utc']):
            ident=str(value['payload']['question']);store.receipt(db,ident,value,at,source_id)
            store.task(db,ident,{'stage':'accepted','historical_local_record':True,'confirmed_at_utc':value['confirmed_at_utc']},at,source_id)
        store.save(db,'imports',(source_id,'local-market-pulse',None,at,clock(),encoded({'native_local_archive':str(root),
            'confirmed_receipt_versions':len(receipts),'matched_native_results':len(results),'material_packages':len(packages),
            'not_a_fresh_platform_read':True}).decode()))
    return {'status':'imported','confirmed_questions':len(ids),'receipt_versions':len(receipts),
        'matched_native_results':len(results),'material_packages':len(packages)}
