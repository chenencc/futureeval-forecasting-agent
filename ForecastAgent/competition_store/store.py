"""Competition identities, original objects, evidence and receipt-bound forecasts."""
import hashlib
import json
from pathlib import Path
import sqlite3
from ForecastAgent.market_store.store import clock, digest, encoded
from ForecastAgent.market_store.web_materials import clean, safe_link

SCHEMA='competition-web-store-v1'

def connect(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,stream TEXT NOT NULL,run_id TEXT,
      observed_at TEXT NOT NULL,imported_at TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS questions(key TEXT PRIMARY KEY,question_id TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS question_versions(id TEXT PRIMARY KEY,question_key TEXT NOT NULL REFERENCES questions(key),
      tournament TEXT NOT NULL,post_id TEXT,kind TEXT,title TEXT,status TEXT,observed_at TEXT NOT NULL,
      source_id TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS task_versions(id TEXT PRIMARY KEY,question_key TEXT NOT NULL REFERENCES questions(key),
      stage TEXT,observed_at TEXT NOT NULL,source_id TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS native_objects(sha256 TEXT PRIMARY KEY,bytes BLOB NOT NULL,size_bytes INTEGER NOT NULL,codec TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS file_refs(id TEXT PRIMARY KEY,source_id TEXT NOT NULL,question_key TEXT,
      path TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL,availability TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS forecasts(id TEXT PRIMARY KEY,question_key TEXT NOT NULL REFERENCES questions(key),
      candidate_sha256 TEXT NOT NULL,selection TEXT,observed_at TEXT NOT NULL,source_id TEXT NOT NULL,
      payload TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,question_key TEXT NOT NULL REFERENCES questions(key),
      forecast_id TEXT REFERENCES forecasts(id),status TEXT NOT NULL,confirmed_at TEXT,verified_binding INTEGER NOT NULL,
      observed_at TEXT NOT NULL,source_id TEXT NOT NULL,payload TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bundles(id TEXT PRIMARY KEY,question_key TEXT NOT NULL REFERENCES questions(key),
      role TEXT NOT NULL,bundle_sha256 TEXT NOT NULL,observed_at TEXT NOT NULL,source_id TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS documents(display_sha256 TEXT PRIMARY KEY,text TEXT NOT NULL,byte_length INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES bundles(id),url TEXT,
      body_sha256 TEXT NOT NULL,display_sha256 TEXT REFERENCES documents(display_sha256),captured_at TEXT,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,question_key TEXT NOT NULL REFERENCES questions(key),
      kind TEXT NOT NULL,occurred_at TEXT,source_sha256 TEXT,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS model_inputs(id TEXT PRIMARY KEY,question_key TEXT NOT NULL REFERENCES questions(key),
      forecast_id TEXT NOT NULL REFERENCES forecasts(id),batch_id TEXT REFERENCES bundles(id),status TEXT NOT NULL,
      state_sha256 TEXT,observed_at TEXT NOT NULL,data TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS question_clock ON question_versions(question_key,observed_at);
    CREATE INDEX IF NOT EXISTS task_clock ON task_versions(question_key,observed_at);
    CREATE INDEX IF NOT EXISTS bundle_clock ON bundles(question_key,observed_at);
    CREATE INDEX IF NOT EXISTS forecast_clock ON forecasts(question_key,observed_at);
    CREATE INDEX IF NOT EXISTS receipt_clock ON receipts(question_key,observed_at);
    CREATE INDEX IF NOT EXISTS ref_question ON file_refs(question_key,path);
    CREATE VIEW IF NOT EXISTS latest_questions AS SELECT * FROM question_versions q WHERE q.rowid=(
      SELECT x.rowid FROM question_versions x WHERE x.question_key=q.question_key ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1);
    CREATE VIEW IF NOT EXISTS latest_tasks AS SELECT * FROM task_versions q WHERE q.rowid=(
      SELECT x.rowid FROM task_versions x WHERE x.question_key=q.question_key ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1);
    CREATE VIEW IF NOT EXISTS latest_forecasts AS SELECT * FROM forecasts q WHERE q.rowid=(
      SELECT x.rowid FROM forecasts x WHERE x.question_key=q.question_key ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1);
    CREATE VIEW IF NOT EXISTS latest_receipts AS SELECT * FROM receipts q WHERE q.rowid=(
      SELECT x.rowid FROM receipts x WHERE x.question_key=q.question_key ORDER BY COALESCE(x.confirmed_at,x.observed_at) DESC,x.rowid DESC LIMIT 1);
    CREATE VIEW IF NOT EXISTS question_overview AS SELECT q.key,q.question_id,v.tournament,v.post_id,v.kind,v.title,
      v.status AS platform_status,t.stage AS worker_stage,f.id AS forecast_id,r.status AS delivery_status,
      r.verified_binding AS receipt_binding_verified,r.confirmed_at,
      (SELECT count(*) FROM captures c JOIN bundles b ON b.id=c.batch_id WHERE b.question_key=q.key) AS capture_versions
      FROM questions q LEFT JOIN latest_questions v ON v.question_key=q.key LEFT JOIN latest_tasks t ON t.question_key=q.key
      LEFT JOIN latest_forecasts f ON f.question_key=q.key LEFT JOIN latest_receipts r ON r.question_key=q.key;
    ''')
    old=db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
    if old and old[0]!=SCHEMA: raise ValueError('Unknown competition database version')
    db.execute("INSERT OR IGNORE INTO metadata VALUES('schema',?)",(SCHEMA,));db.commit()
    return db

def save(db,table,values):
    db.execute('INSERT OR IGNORE INTO '+table+' VALUES('+','.join('?' for _ in values)+')',values)

def identify(db,ident):
    ident=str(ident)
    if not ident.isdecimal(): raise ValueError('Exact Metaculus question ID required')
    key='metaculus:'+ident;save(db,'questions',(key,ident));return key

def native_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def question(db,value,tournament,observed,source):
    key=identify(db,value.get('question_id',value.get('id')))
    allowed=('id','question_id','post_id','type','question_type','title','question','resolution_criteria','fine_print',
      'description','status','open_time','close_time','actual_close_time','scheduled_close_time','scheduled_resolve_time',
      'spot_scoring_time','deadline_utc','submission_deadline_utc','options','all_options_ever','scaling','unit',
      'inbound_outcome_count','open_lower_bound','open_upper_bound','resolution','actual_resolve_time','url')
    value={k:value[k] for k in allowed if k in value}
    row={'key':key,'tournament':tournament,'observed_at':observed,'source_id':source,'value':value}
    save(db,'question_versions',(digest(row),key,tournament,str(value.get('post_id')) if value.get('post_id') is not None else None,
      value.get('type') or value.get('question_type'),value.get('title') or value.get('question'),value.get('status'),observed,source,encoded(value).decode()))
    return key

def task(db,ident,value,observed,source):
    key=identify(db,ident)
    save(db,'task_versions',(digest({'key':key,'value':value,'at':observed,'source':source}),key,value.get('stage') or value.get('status'),observed,source,encoded(value).decode()))
    for item in value.get('history',[]): event(db,key,'task_stage',item,item.get('at_utc'))

def event(db,key,kind,value,at=None,sha=None):
    save(db,'events',(digest({'key':key,'kind':kind,'value':value,'at':at}),key,kind,at,sha,encoded(value).decode()))

def candidate(db,ident,value,observed,source):
    key=identify(db,ident);payload=value.get('payload') or {}
    if str(payload.get('question'))!=str(ident): raise ValueError('Candidate question ID differs')
    sha=digest(value);fid=digest({'key':key,'candidate':sha})
    save(db,'forecasts',(fid,key,sha,value.get('selection'),observed,source,encoded(payload).decode(),encoded(value).decode()))
    return fid

def receipt(db,ident,value,observed,source):
    key=identify(db,ident);payload=value.get('payload') or {}
    if str(payload.get('question'))!=str(ident): raise ValueError('Receipt question ID differs')
    match=db.execute('SELECT id FROM forecasts WHERE question_key=? AND payload=? ORDER BY observed_at DESC,rowid DESC LIMIT 1',(key,encoded(payload).decode())).fetchone()
    confirmed=value.get('confirmed_at_utc');status=value.get('status','not_recorded')
    if confirmed:
        from datetime import datetime
        if datetime.fromisoformat(confirmed.replace('Z','+00:00')).tzinfo is None: raise ValueError('Receipt clock needs timezone')
    verified=status=='accepted' and bool(confirmed) and match is not None
    public_status='accepted' if verified else 'accepted_unbound' if status=='accepted' and confirmed else 'unconfirmed' if status=='accepted' else status
    save(db,'receipts',(digest({'key':key,'receipt':value}),key,match[0] if match else None,public_status,confirmed,int(verified),observed,source,encoded(payload).decode(),encoded(value).decode()))

def bundle(db,ident,value,role,observed,source):
    key=identify(db,ident)
    if str(value.get('request',{}).get('id'))!=str(ident): raise ValueError('Material question ID differs')
    sha=digest(value);bid=digest({'key':key,'sha':sha,'role':role})
    summary={k:v for k,v in value.items() if k not in ('pages','transcript','messages','searches','exa_searches','fetch_attempts','model_attempts','quarantine','evidence')}
    summary['original_full_bundle_retained_in_native_objects']=True
    save(db,'bundles',(bid,key,role,sha,observed,source,encoded(summary).decode()))
    for url,page in value.get('pages',{}).items():
        text=page.get('content')
        if not isinstance(text,str) or not text: continue
        original=hashlib.sha256(text.encode()).hexdigest()
        if page.get('content_sha256') and page['content_sha256']!=original: raise ValueError('Original body hash differs')
        display=clean(text);display_sha=hashlib.sha256(display.encode()).hexdigest()
        usable=page.get('body_diagnostics',{}).get('usable_text') is not False
        save(db,'documents',(display_sha,display,len(display.encode())))
        at=page.get('retrieved_at_utc') or page.get('captured_at_utc') or page.get('fetched_at') or page.get('retrieved_at')
        metadata={k:v for k,v in page.items() if k!='content'};metadata['usable_text']=usable
        save(db,'captures',(digest({'batch':bid,'url':url,'sha':original}),bid,url,original,display_sha,at,encoded(metadata).decode()))
    for kind,field in (('search','searches'),('exa_search','exa_searches'),('fetch','fetch_attempts'),('acquisition_model','model_attempts')):
        for item in value.get(field,[]):event(db,key,kind,item,item.get('attempted_at_utc'),sha)
    return bid

def bind_input(db,ident,forecast_id,batch_id,objects,observed):
    """Bind the saved selected model input through its native request identity."""
    candidate_value=objects.get('candidate.json') or {};selection=candidate_value.get('selection')
    stage='second' if selection=='mercury_conditional_reread' else 'first'
    prefix='analysis-core-1.0.1/mercury-v1.0.1/'
    if prefix+'result.json' not in objects: prefix='mercury-v1.0.1/'
    state=objects.get(prefix+stage+'-state.json');request=objects.get(prefix+stage+'/request.json')
    identity=objects.get(prefix+stage+'/identity.json');result=objects.get(prefix+'result.json')
    payload={'selection':selection,'reason':'Saved selected input chain not present'};status='input_not_bound'
    if state is not None and request and identity and result:
        frozen=db.execute('SELECT data FROM bundles WHERE id=? AND question_key=?',(batch_id,'metaculus:'+str(ident))).fetchone()
        frozen_request=json.loads(frozen[0]).get('request',{}) if frozen else {}
        state_question=state.get('question') or {}
        good=(request.get('state')==state and identity.get('request_sha256')==native_hash(request) and
            candidate_value.get('model_result_sha256')==native_hash(result) and result.get('payload')==candidate_value.get('payload') and
            result.get('selection')==selection and isinstance(state_question,dict) and
            str(state_question.get('id'))==str(ident) and frozen is not None and
            state_question.get('resolution_criteria')==frozen_request.get('resolution_criteria'))
        status='model_input_verified' if good else 'integrity_failed'
        payload={'selection':selection,'state':state if good else None,'model':request.get('model') if good else None,
                 'request_sha256':identity.get('request_sha256'),'reason':None if good else 'Native selected input/result binding differs',
                 'span_verification':'native_saved_input_only_not_independent_truth_verification'}
    key=identify(db,ident);sha=native_hash(state) if state is not None else None
    save(db,'model_inputs',(digest({'forecast':forecast_id,'batch':batch_id,'status':status,'state':sha}),key,forecast_id,batch_id,status,sha,observed,encoded(payload).decode()))

def stats(db):
    tables=('questions','question_versions','task_versions','forecasts','receipts','bundles','captures','documents','events','native_objects','file_refs')
    result={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables}
    for name,sql in {
      'types':'SELECT kind,count(*) FROM latest_questions GROUP BY kind',
      'task_stages':'SELECT stage,count(*) FROM latest_tasks GROUP BY stage',
      'delivery':'SELECT status,count(*) FROM latest_receipts GROUP BY status',
      'input_bindings':'SELECT status,count(*) FROM model_inputs GROUP BY status'}.items():result[name]={str(r[0]):r[1] for r in db.execute(sql)}
    return result
