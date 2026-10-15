"""SQLite projections plus append-only, idempotent replication records.

Large acquisition bodies remain in the existing content-addressed archive.
This database is separate from the generic document index and native labels.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

SCHEMA = 'forecast-market-store-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def clock():
    return datetime.now(timezone.utc).isoformat()


def epoch(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('A timezone is required')
    return int(result.timestamp())


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS records(seq INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,
      kind TEXT NOT NULL,market_key TEXT,observed_at TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS replication_receipts(id TEXT PRIMARY KEY,original_record_id TEXT NOT NULL,
      projected_record_id TEXT NOT NULL,kind TEXT NOT NULL,market_key TEXT,observed_at TEXT NOT NULL,
      payload_sha256 TEXT NOT NULL,resolution TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS markets(key TEXT PRIMARY KEY,venue TEXT NOT NULL,venue_id TEXT NOT NULL,
      yes_token TEXT,family TEXT,condition_id TEXT,created_at TEXT NOT NULL,identity_sha256 TEXT NOT NULL,
      question TEXT,url TEXT,rules TEXT,rules_sha256 TEXT);
    CREATE TABLE IF NOT EXISTS task_versions(sha256 TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      status TEXT,stage TEXT,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS task_observations(id TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      payload_sha256 TEXT NOT NULL REFERENCES task_versions(sha256),observed_at TEXT NOT NULL,source TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS task_time ON task_observations(market_key,observed_at);
    CREATE TABLE IF NOT EXISTS forecasts(id TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      arm TEXT NOT NULL,sealed_at TEXT,probability_yes REAL CHECK(probability_yes BETWEEN 0 AND 1),
      status TEXT,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      revision_id TEXT NOT NULL,material_sha256 TEXT,graph_sha256 TEXT,forecast_status TEXT,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS native_tracking(id TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      observed_at TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS realtime_quotes(id TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      received_at TEXT NOT NULL,role TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS material_refs(id TEXT PRIMARY KEY,market_key TEXT REFERENCES markets(key),
      path TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER,source TEXT NOT NULL,
      availability TEXT NOT NULL DEFAULT 'remote_archive_reference');
    CREATE TABLE IF NOT EXISTS pack_locations(id TEXT PRIMARY KEY,market_key TEXT REFERENCES markets(key),
      sha256 TEXT NOT NULL,path TEXT NOT NULL,observed_at TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS venue_observations(id TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      observed_at TEXT NOT NULL,status TEXT NOT NULL,result TEXT,raw_sha256 TEXT NOT NULL,data TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS venue_time ON venue_observations(market_key,observed_at);
    CREATE TABLE IF NOT EXISTS price_points(id TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      kind TEXT NOT NULL,point_at INTEGER NOT NULL,resolution_seconds INTEGER NOT NULL,
      price REAL NOT NULL CHECK(price BETWEEN 0 AND 1),bid REAL,ask REAL,
      observed_at TEXT NOT NULL,response_sha256 TEXT NOT NULL,run_id TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS price_time ON price_points(market_key,kind,point_at,resolution_seconds);
    CREATE TABLE IF NOT EXISTS history_windows(id TEXT PRIMARY KEY,market_key TEXT NOT NULL REFERENCES markets(key),
      start_at INTEGER NOT NULL,end_at INTEGER NOT NULL,status TEXT NOT NULL,observed_at TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cursors(market_key TEXT PRIMARY KEY REFERENCES markets(key),scanned_until INTEGER,
      last_point_at INTEGER,retry_at INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS imports(source_id TEXT PRIMARY KEY,imported_at TEXT NOT NULL,sha256 TEXT NOT NULL);
    CREATE VIEW IF NOT EXISTS latest_tasks AS SELECT t.*,o.observed_at,o.source FROM task_observations o
      JOIN task_versions t ON t.sha256=o.payload_sha256
      WHERE COALESCE(json_extract(o.source,'$.stream'),'market-worker')='market-worker'
        AND o.rowid=(SELECT x.rowid FROM task_observations x WHERE x.market_key=o.market_key
        AND COALESCE(json_extract(x.source,'$.stream'),'market-worker')='market-worker'
        ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1);
    CREATE VIEW IF NOT EXISTS latest_discovery AS SELECT t.*,o.observed_at,o.source FROM task_observations o
      JOIN task_versions t ON t.sha256=o.payload_sha256 WHERE json_extract(o.source,'$.stream')='market-monitor'
        AND o.rowid=(SELECT x.rowid FROM task_observations x WHERE x.market_key=o.market_key
        AND json_extract(x.source,'$.stream')='market-monitor' ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1);
    CREATE VIEW IF NOT EXISTS latest_venue AS SELECT * FROM venue_observations v WHERE v.rowid=(
      SELECT x.rowid FROM venue_observations x WHERE x.market_key=v.market_key ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1);
    CREATE VIEW IF NOT EXISTS latest_prices AS SELECT * FROM price_points p WHERE p.rowid=(
      SELECT x.rowid FROM price_points x WHERE x.market_key=p.market_key AND x.kind=p.kind AND x.point_at=p.point_at
        AND x.resolution_seconds=p.resolution_seconds ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1);
    CREATE VIEW IF NOT EXISTS market_overview AS SELECT m.key,m.venue,m.question,m.created_at,
      t.status AS pipeline_status,t.stage AS pipeline_stage,t.observed_at AS pipeline_observed_at,
      v.status AS venue_status,v.result AS final_result,v.observed_at AS venue_observed_at,
      c.scanned_until,c.last_point_at,c.status AS history_status,c.retry_at,
      (SELECT count(*) FROM forecasts f WHERE f.market_key=m.key) AS forecast_versions,
      (SELECT count(*) FROM price_points p WHERE p.market_key=m.key) AS price_versions
      FROM markets m LEFT JOIN latest_tasks t ON t.market_key=m.key
      LEFT JOIN latest_venue v ON v.market_key=m.key LEFT JOIN cursors c ON c.market_key=m.key;
    ''')
    current = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
    if current and current[0] != SCHEMA:
        raise ValueError('Unknown database schema')
    db.execute("INSERT OR IGNORE INTO metadata VALUES('schema',?)", (SCHEMA,))
    db.commit()
    return db


def identity(market):
    fields = ('key', 'venue', 'venue_id', 'yes_token', 'family', 'condition_id', 'created_at')
    value = {k: market.get(k) for k in fields}
    if value['key'] != value['venue'] + ':' + str(value['venue_id']):
        raise ValueError('Market key differs from venue identity')
    epoch(value['created_at'])
    if value['venue'] == 'polymarket':
        if not str(value['yes_token'] or '').isdigit():
            raise ValueError('Exact frozen YES token is required')
    elif value['venue'] == 'kalshi':
        if not str(value['family'] or '').startswith('kalshi:'):
            raise ValueError('Exact frozen event ticker is required')
    else:
        raise ValueError('Unsupported venue')
    return value


def append(db, kind, key, data, observed_at, *, ident=None):
    """Project one immutable replication record in a caller-owned transaction."""
    epoch(observed_at)
    observed_at=datetime.fromisoformat(observed_at.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
    payload = {'kind': kind, 'key': key, 'data': data, 'observed_at': observed_at}
    ident = ident or digest(payload)
    old = db.execute('SELECT data,kind,market_key,observed_at FROM records WHERE id=?', (ident,)).fetchone()
    if old:
        if (old['data'], old['kind'], old['market_key'], old['observed_at']) == (encoded(data).decode(), kind, key, observed_at):
            return False
        # Content identity and publication observation are separate. Cloud history
        # and direct catalogs can first observe the same immutable payload at
        # different times. Only explicitly defined content IDs can be reconciled.
        if kind == 'task_payload':
            expected = digest({'kind':kind,'key':key,'sha256':digest(data)})
        elif kind in {'forecast','quote'}:
            expected = digest({'kind':kind,'key':key,'data':data})
        elif kind == 'ref':
            expected = digest({'kind':kind,'key':key,'path':data['path'],'sha256':data['sha256']})
        else:
            expected = None
        if ident != expected or old['kind'] != kind or old['market_key'] != key:
            raise ValueError('Replication record collision')
        previous = json.loads(old['data'])
        if previous == data:
            resolution, projected = 'content_reobserved', ident
            added = False
        elif kind == 'ref' and {k:v for k,v in previous.items() if k!='source'} == {k:v for k,v in data.items() if k!='source'}:
            # Preserve additional archive locations; never overwrite the first
            # reference or discard the later source's provenance.
            resolution = 'additional_reference_location'
            projected = digest({'kind':'ref_observation','record_id':ident,'data':data,'observed_at':observed_at})
            added = append(db,kind,key,data,observed_at,ident=projected)
        else:
            raise ValueError('Replication record collision')
        receipt = digest({'record_id':ident,'projected_id':projected,'observed_at':observed_at,'data':data})
        db.execute('INSERT OR IGNORE INTO replication_receipts VALUES(?,?,?,?,?,?,?,?)',
            (receipt,ident,projected,kind,key,observed_at,digest(data),resolution))
        return added
    text = encoded(data).decode()
    if kind == 'market':
        bound = identity(data)
        saved = db.execute('SELECT identity_sha256 FROM markets WHERE key=?', (key,)).fetchone()
        if saved and saved[0] != digest(bound):
            raise ValueError('Frozen market identity changed')
        db.execute('INSERT OR IGNORE INTO markets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (key, data['venue'], str(data['venue_id']), data.get('yes_token'), data.get('family'),
             data.get('condition_id'), data['created_at'], digest(bound), data.get('question'), data.get('url'),
             data.get('rules'), data.get('rules_sha256')))
    elif kind == 'task_payload':
        task=data
        sha=digest(task)
        db.execute('INSERT OR IGNORE INTO task_versions VALUES(?,?,?,?,?)',
            (sha,key,task.get('status'),task.get('stage'),encoded(task).decode()))
    elif kind == 'task':
        sha=data['payload_sha256']
        db.execute('INSERT INTO task_observations VALUES(?,?,?,?,?)', (ident,key,sha,observed_at,encoded(data['source']).decode()))
    elif kind == 'forecast':
        p = data['value'].get('eventual_probability_yes')
        if p is not None and (isinstance(p,bool) or not isinstance(p,(int,float)) or not 0 <= p <= 1):
            raise ValueError('Invalid forecast probability')
        db.execute('INSERT INTO forecasts VALUES(?,?,?,?,?,?,?)',
            (ident,key,data['arm'],data.get('sealed_at'),p,data['value'].get('status'),text))
    elif kind == 'revision':
        db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?)',
            (ident,key,data['revision_id'],data.get('material_sha256'),data.get('graph_sha256'),data.get('forecast_status'),text))
    elif kind == 'tracking':
        db.execute('INSERT INTO native_tracking VALUES(?,?,?,?)',(ident,key,observed_at,text))
    elif kind == 'quote':
        db.execute('INSERT INTO realtime_quotes VALUES(?,?,?,?,?)',
            (ident,key,data['received_at'],data['role'],text))
    elif kind == 'ref':
        db.execute('INSERT INTO material_refs(id,market_key,path,sha256,size_bytes,source,availability) VALUES(?,?,?,?,?,?,?)',
            (ident,key,data['path'],data['sha256'],data.get('size_bytes'),encoded(data['source']).decode(),data.get('availability','remote_archive_reference')))
    elif kind == 'pack':
        db.execute('INSERT INTO pack_locations VALUES(?,?,?,?,?,?)',
            (ident,key,data['sha256'],data['path'],observed_at,text))
    elif kind == 'venue':
        db.execute('INSERT INTO venue_observations VALUES(?,?,?,?,?,?,?)',
            (ident,key,observed_at,data['status'],data.get('result'),data['raw_sha256'],text))
    elif kind == 'point':
        db.execute('INSERT INTO price_points VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (ident,key,data['kind'],data['point_at'],data['resolution_seconds'],data['price'],data.get('bid'),
             data.get('ask'),observed_at,data['response_sha256'],data['run_id']))
    elif kind == 'window':
        db.execute('INSERT INTO history_windows VALUES(?,?,?,?,?,?,?)',
            (ident,key,data['start_at'],data['end_at'],data['status'],observed_at,text))
    elif kind == 'cursor':
        previous=db.execute('SELECT data FROM cursors WHERE market_key=?',(key,)).fetchone()
        if previous and datetime.fromisoformat(json.loads(previous[0])['observed_at'].replace('Z','+00:00'))>datetime.fromisoformat(observed_at):
            db.execute('INSERT INTO records(id,kind,market_key,observed_at,data) VALUES(?,?,?,?,?)', (ident,kind,key,observed_at,text))
            return True
        db.execute('INSERT INTO cursors VALUES(?,?,?,?,?,?) ON CONFLICT(market_key) DO UPDATE SET '
            'scanned_until=excluded.scanned_until,last_point_at=excluded.last_point_at,retry_at=excluded.retry_at,'
            'status=excluded.status,data=excluded.data',
            (key,data.get('scanned_until'),data.get('last_point_at'),data.get('retry_at',0),data['status'],text))
    else:
        raise ValueError('Unknown replication record kind')
    db.execute('INSERT INTO records(id,kind,market_key,observed_at,data) VALUES(?,?,?,?,?)', (ident,kind,key,observed_at,text))
    return True


def records_since(db, seq):
    for row in db.execute('SELECT id,kind,market_key,observed_at,data FROM records WHERE seq>? ORDER BY seq', (seq,)):
        yield {'id':row['id'],'kind':row['kind'],'key':row['market_key'],
               'observed_at':row['observed_at'],'data':json.loads(row['data'])}


def ingest_summary(db, summary, source):
    observed = summary['generated_at']
    from ForecastAgent.pipeline_health import observations
    observations(db, summary, source)
    for content in summary['sources'].values():
        for task in content.get('state.json', {}).get('tasks', []):
            market, key = task['market'], task['id']
            identity(market)
            # Identity and forecast payloads are saved once across repeated catalogs.
            if not db.execute('SELECT 1 FROM markets WHERE key=?',(key,)).fetchone():
                append(db,'market',key,market,observed)
            else:
                saved=db.execute('SELECT identity_sha256 FROM markets WHERE key=?',(key,)).fetchone()[0]
                if saved != digest(identity(market)):
                    raise ValueError('Catalog changes frozen identity')
            token=digest({'kind':'task_payload','key':key,'sha256':digest(task)})
            if not db.execute('SELECT 1 FROM records WHERE id=?',(token,)).fetchone():
                append(db,'task_payload',key,task,observed,ident=token)
            append(db,'task',key,{'payload_sha256':digest(task),'source':source},observed)
            forecast = task.get('forecast') or {}
            quote=forecast.get('anchor') or {}
            if quote.get('received_at'):
                data={**quote,'role':'original_forecast_anchor'}
                token=digest({'kind':'quote','key':key,'data':data})
                if not db.execute('SELECT 1 FROM records WHERE id=?',(token,)).fetchone():
                    append(db,'quote',key,data,quote['received_at'],ident=token)
            for arm, value in forecast.get('arms', {}).items():
                data={'arm':arm,'sealed_at':value.get('sealed_at') or forecast.get('sealed_at'),
                      'anchor_at':forecast.get('anchor_at'),'value':value}
                token=digest({'kind':'forecast','key':key,'data':data})
                if not db.execute('SELECT 1 FROM records WHERE id=?',(token,)).fetchone():
                    append(db,'forecast',key,data,observed,ident=token)
        for key,tracking in content.get('tracking.json',{}).get('tasks',{}).items():
            if not db.execute('SELECT 1 FROM markets WHERE key=?',(key,)).fetchone(): continue
            append(db,'tracking',key,tracking,tracking.get('observed_at') or observed)
            for value in tracking.get('prices',{}).values():
                quote=value.get('quote') or {}
                if not quote.get('received_at'): continue
                data={**quote,'role':'native_followup_window_quote'}
                token=digest({'kind':'quote','key':key,'data':data})
                if not db.execute('SELECT 1 FROM records WHERE id=?',(token,)).fetchone():
                    append(db,'quote',key,data,quote['received_at'],ident=token)


def stats(db):
    tables=('markets','task_versions','forecasts','revisions','native_tracking','realtime_quotes',
            'material_refs','price_points','history_windows','venue_observations')
    result={name:db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in tables}
    result['history_states']={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM cursors GROUP BY status')}
    result['pipeline_states']={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM latest_tasks GROUP BY status')}
    return result
