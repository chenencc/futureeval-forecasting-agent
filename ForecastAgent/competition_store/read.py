"""Read-only competition Web DTOs; no native archive/network side effects."""
import hashlib
import json
from pathlib import Path
import sqlite3
from ForecastAgent.market_store.store import clock
from ForecastAgent.market_store.web_materials import clean,safe_link

def open_readonly(root):
    path=(Path(root)/'competition.sqlite3').resolve()
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');return db

def read_index(db):
    return {'schema':'competition-web-index-v1','generated_at':clock(),'read_only':True,
        'tasks':[dict(r) for r in db.execute('SELECT * FROM question_overview ORDER BY question_id')],
        'watermark':watermark(db)}

def watermark(db):
    values={name:db.execute('SELECT count(*),max(rowid) FROM '+name).fetchone()[:] for name in ('imports','question_versions','task_versions','forecasts','receipts','bundles','model_inputs')}
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()

def read_body(db,sha):
    import re
    if not re.fullmatch('[0-9a-f]{64}',sha): raise ValueError('Invalid display body hash')
    row=db.execute('SELECT text,byte_length FROM documents WHERE display_sha256=?',(sha,)).fetchone()
    if row is None: raise KeyError('Body not imported')
    raw=row[0].encode()
    if len(raw)!=row[1] or hashlib.sha256(raw).hexdigest()!=sha: raise ValueError('Stored display body integrity differs')
    return raw

def sources(db,bid):
    result=[]
    for row in db.execute('SELECT * FROM captures WHERE batch_id=? ORDER BY rowid',(bid,)):
        meta=json.loads(row['data']);text=read_body(db,row['display_sha256']).decode()
        result.append({'id':row['id'],'url':safe_link(row['url']),'title':clean(meta.get('title') or row['url']),
            'body_sha256':row['body_sha256'],'display_sha256':row['display_sha256'],
            'captured_at':row['captured_at'],'usable_text':meta.get('usable_text'),
            'body_ref':'/api/competition/bodies/'+row['display_sha256'],
            'excerpt':text[:1800],'excerpt_truncated':len(text)>1800,
            'body_diagnostics':clean(meta.get('body_diagnostics'))})
    return result

def read_task(db,key):
    overview=db.execute('SELECT * FROM question_overview WHERE key=?',(key,)).fetchone()
    if overview is None: raise KeyError('Unknown observed competition question')
    q=db.execute('SELECT data FROM latest_questions WHERE question_key=?',(key,)).fetchone()
    task=db.execute('SELECT data FROM latest_tasks WHERE question_key=?',(key,)).fetchone()
    forecasts=[]
    for row in db.execute('SELECT * FROM forecasts WHERE question_key=? ORDER BY observed_at,rowid',(key,)):
        value=json.loads(row['data'])
        forecasts.append({'id':row['id'],'candidate_sha256':row['candidate_sha256'],'selection':row['selection'],
            'observed_at':row['observed_at'],'payload':json.loads(row['payload']),
            'release_version':value.get('release_version'),'model_result_sha256':value.get('model_result_sha256')})
    receipts=[]
    for row in db.execute('SELECT * FROM receipts WHERE question_key=? ORDER BY observed_at,rowid',(key,)):
        value=json.loads(row['data'])
        receipts.append({'id':row['id'],'forecast_id':row['forecast_id'],'status':row['status'],
            'confirmed_at':row['confirmed_at'],'verified_binding':bool(row['verified_binding']),
            'submitted_payload':json.loads(row['payload']),'comment_id':value.get('comment_id'),
            'forecast_start_time':value.get('forecast_start_time'),'observed_at':row['observed_at']})
    batches=[]
    for row in db.execute('SELECT * FROM bundles WHERE question_key=? ORDER BY observed_at,rowid',(key,)):
        value=json.loads(row['data'])
        batches.append({'id':row['id'],'role':row['role'],'bundle_sha256':row['bundle_sha256'],
            'observed_at':row['observed_at'],'request':clean(value.get('request')),
            'gaps':clean(value.get('gaps') or value.get('result',{}).get('gaps',[])),
            'plan':clean(value.get('plan',[])),'sources':sources(db,row['id'])})
    inputs=[]
    for row in db.execute('SELECT * FROM model_inputs WHERE question_key=? ORDER BY observed_at,rowid',(key,)):
        saved=json.loads(row['data']);state=saved.get('state') or {};status=row['status']
        pairs={(r['url'],r['body_sha256']) for r in db.execute('SELECT url,body_sha256 FROM captures WHERE batch_id=?',(row['batch_id'],))}
        missing=[s.get('source_id') for s in state.get('sources',[]) if (s.get('url'),s.get('body_sha256')) not in pairs]
        if status=='model_input_verified' and missing: status='source_binding_unavailable'
        inputs.append({'id':row['id'],'forecast_id':row['forecast_id'],'batch_id':row['batch_id'],
            'status':status,'model':saved.get('model'),'state_sha256':row['state_sha256'],
            'evidence':clean(state.get('evidence',[])) if status=='model_input_verified' else [],
            'sources':clean(state.get('sources',[])) if status=='model_input_verified' else [],
            'research_map':clean(state.get('research_map')) if status=='model_input_verified' else None,
            'missing_source_ids':missing,'reason':saved.get('reason')})
    events=[]
    for row in db.execute('SELECT id,kind,occurred_at,data FROM events WHERE question_key=? ORDER BY rowid',(key,)):
        value=json.loads(row['data'])
        events.append({'id':row['id'],'kind':row['kind'],'occurred_at':row['occurred_at'],
            'status':value.get('status'),'query':clean(value.get('query')),'url':safe_link(value.get('url')),
            'tool':value.get('tool'),'error':clean(value.get('error')),
            'model':(value.get('request') or {}).get('model'),
            'reported_usage':(value.get('response') or {}).get('usage')})
    return {'schema':'competition-web-detail-v1','read_only':True,'overview':dict(overview),
        'question':clean(json.loads(q[0])) if q else None,'native_task':clean(json.loads(task[0])) if task else None,
        'forecasts':forecasts,'receipts':receipts,'material_batches':batches,'decision_inputs':inputs,
        'events':events,'availability':{'material_batches':len(batches),'bound_model_inputs':sum(i['status']=='model_input_verified' for i in inputs)},
        'warning':'Saved materials are not verified truth. Only bound accepted readback receipts count as submitted.'}

def main():
    import argparse
    from .ingest import sync
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('sync','status','export'))
    p.add_argument('--root',default='E:/metaculus_data/competitions/store');p.add_argument('--output')
    args=p.parse_args()
    if args.command=='sync': print(json.dumps(sync(args.root),ensure_ascii=False));return
    db=open_readonly(args.root)
    try:
        db.execute('BEGIN');value=read_index(db)
        if args.command=='export':
            if not args.output:p.error('--output required')
            output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
            for item in value['tasks']:
                detail=read_task(db,item['key']);(output/(item['question_id']+'.json')).write_text(json.dumps(detail,ensure_ascii=False),encoding='utf-8')
            (output/'index.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(value if args.command=='status' else {'questions':len(value['tasks']),'output':str(output)},ensure_ascii=False))
        db.rollback()
    finally:db.close()

if __name__=='__main__':main()
