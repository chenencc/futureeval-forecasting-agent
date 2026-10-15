"""Read-only Web DTOs; no native archive parsing is required by the consumer."""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .store import clock,encoded
from .web_materials import ARMS,clean,fingerprint,hydrate,safe_link


def open_readonly(root):
    path=(Path(root)/'market.sqlite3').resolve()
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def instant(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('Timezone required')
    return t.astimezone(timezone.utc)


def iso(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat() if value is not None else None


def quote(value):
    if not isinstance(value,dict): return None
    fields=('status','received_at','midpoint','yes_bid','yes_ask','spread','raw_book_sha256')
    return clean({k:value.get(k) for k in fields})


def valid_arms(forecast):
    return sum(v.get('status')=='completed' and type(v.get('eventual_probability_yes')) in (int,float)
        and 0<=v['eventual_probability_yes']<=1 for v in forecast.get('arms',{}).values())


def public_forecast(value):
    fields=('status','sealed_at','anchor_at','anchor','arms','price_schedule','forecast_before_targets',
            'timely','completed_at','sealed_at_role')
    result={k:value[k] for k in fields if k in value}
    arm_fields=('status','reason','error_type','eventual_probability_yes','price_means','map_status',
        'map_delivered','response_sha256','state_sha256','sealed_at','timely','seal_latency_minutes')
    result['arms']={arm:{k:v[k] for k in arm_fields if k in v} for arm,v in value.get('arms',{}).items()}
    return clean(result)


def batch(db,key,ident,kind,forecast,result_sha=None,include_materials=True):
    available=db.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='latest_web_packets'").fetchone()
    columns='*' if include_materials else "material_status,observed_at,json_array_length(payload,'$.materials.sources') AS source_count"
    packet=db.execute('SELECT '+columns+' FROM latest_web_packets WHERE market_key=? AND batch_id=? AND forecast_fingerprint=?',
        (key,ident,fingerprint(forecast))).fetchone() if available else None
    value=json.loads(packet['payload']) if packet and include_materials else {}
    return {'id':ident,'source_kind':kind,'label':'Original worker' if kind=='original_worker' else 'Independent material revision',
        'result_sha256':result_sha,'sealed_at':forecast.get('sealed_at'),'forecast':public_forecast(forecast),
        'materials':value.get('materials'),'material_status':packet['material_status'] if packet else 'pack_not_synced',
        'import_error':value.get('import_error'),'materials_included':include_materials,
        'forecast_fingerprint':fingerprint(forecast),'material_verified_at':packet['observed_at'] if packet else None,
        'source_count':len((value.get('materials') or {}).get('sources',[])) if include_materials else packet['source_count'] if packet else None}


def price_cells(market,tracking,now):
    result=[]
    for hours in (24,72,120,168):
        target=instant(market['created_at'])+timedelta(hours=hours)
        saved=tracking.get('prices',{}).get(str(hours))
        cell={'hours':hours,'target_at':target.isoformat(),'quote':None,'source':'not_observed'}
        if saved:
            if not saved.get('target_at') or instant(saved['target_at'])!=target:
                cell.update(status='target_mismatch',source='rejected_saved_label')
            else:
                cell.update(status=saved['status'],source='saved_monitor_label',quote=quote(saved.get('quote')),
                    terminal_value=saved.get('terminal_value'))
        else:
            scheduled=str(hours) in tracking.get('schedule',{}).get('targets',{})
            status='not_scheduled' if not scheduled else 'waiting' if target>now else 'awaiting_sync'
            cell.update(status=status)
        result.append(cell)
    return result


def read_history(db,key,kind=None,resolution=None,before=None,limit=10000):
    market=db.execute('SELECT * FROM markets WHERE key=?',(key,)).fetchone()
    if market is None: raise KeyError('Unknown enrolled market')
    if not 1<=limit<=10000: raise ValueError('History limit must be 1..10000')
    default='candle_bid_ask_close_midpoint' if market['venue']=='kalshi' else 'provider_history_price'
    kind=kind or default
    allowed={'provider_history_price','provider_unbucketed_terminal_point'} if market['venue']=='polymarket' else {'candle_bid_ask_close_midpoint','candle_trade_close'}
    if kind not in allowed: raise ValueError('Price kind does not belong to this venue')
    if resolution is not None and (type(resolution) is not int or resolution<0): raise ValueError('Invalid resolution')
    if resolution is None:
        resolution=db.execute('SELECT min(resolution_seconds) FROM latest_prices WHERE market_key=? AND kind=?',(key,kind)).fetchone()[0]
    conditions=['market_key=?','kind=?'];params=[key,kind]
    if resolution is not None: conditions.append('resolution_seconds=?');params.append(resolution)
    if before is not None: conditions.append('point_at<?');params.append(int(before))
    rows=list(db.execute('SELECT * FROM latest_prices WHERE '+' AND '.join(conditions)+
        ' ORDER BY point_at DESC,resolution_seconds ASC,observed_at DESC LIMIT ?',(*params,limit+1)))
    truncated=len(rows)>limit
    rows=rows[:limit]
    # Different resolutions remain distinct records; no invented regular time grid.
    points=[{'timestamp':iso(r['point_at']),'price':r['price'],'kind':r['kind'],
        'resolution_seconds':r['resolution_seconds'],'observed_at':r['observed_at'],
        **({'bid':r['bid'],'ask':r['ask']} if r['bid'] is not None else {})} for r in reversed(rows)]
    current=db.execute('SELECT * FROM cursors WHERE market_key=?',(key,)).fetchone()
    cursor=json.loads(current['data']) if current else {}
    newest=max((r['observed_at'] for r in rows),default=None)
    return {'key':key,'venue':market['venue'],'status':'partial' if truncated else 'recorded' if points else 'empty',
        'identity':{'yes_token':market['yes_token'],'condition_id':market['condition_id']} if market['venue']=='polymarket' else
            {'ticker':market['venue_id'],'event_ticker':market['family'].split(':',1)[1]},
        'points':points,'price_kind':kind,'requested_start':points[0]['timestamp'] if points else market['created_at'],
        'selected_resolution_seconds':resolution,
        'requested_end':iso(cursor.get('live_scanned_until') or cursor.get('scanned_until')) or clock(),
        'retrieved_at':newest,'last_success_at':newest,'interval_seconds':7200,
        'history_status':cursor.get('status','not_recorded'),'scanned_until':iso(cursor.get('scanned_until')),
        'live_scanned_until':iso(cursor.get('live_scanned_until')),'retry_at':iso(cursor.get('retry_at') or None),
        'pagination':{'truncated':truncated,'next_before':rows[-1]['point_at'] if rows and truncated else None},
        'available_series':[dict(r) for r in db.execute('SELECT kind,resolution_seconds,count(*) AS records '
            'FROM latest_prices WHERE market_key=? GROUP BY kind,resolution_seconds',(key,))],
        'usage_role':'retrospective_display_only_not_native_evaluation_label'}


def read_task(db,key,*,with_history=True,with_materials=True):
    market=db.execute('SELECT * FROM markets WHERE key=?',(key,)).fetchone()
    if market is None: raise KeyError('Unknown enrolled market')
    market=dict(market)
    native=db.execute('SELECT * FROM latest_tasks WHERE market_key=?',(key,)).fetchone()
    task=json.loads(native['data']) if native else {}
    versions=[]
    if native:
        versions.append(batch(db,key,'initial_worker','original_worker',task.get('forecast') or {},include_materials=with_materials))
    for row in db.execute('SELECT data FROM revisions WHERE market_key=? ORDER BY rowid',(key,)):
        revision=json.loads(row[0])
        versions.append(batch(db,key,revision['revision_id'],'material_revision',revision['payload'].get('forecast') or {},revision['result_sha256'],with_materials))
    versions.sort(key=lambda r:r.get('sealed_at') or '')
    completed=[v for v in versions if valid_arms(v['forecast'])]
    selected=(completed or versions)[-1] if versions else None
    materials=selected.get('materials') if selected else None
    quotes=[quote(json.loads(r[0])) for r in db.execute('SELECT data FROM realtime_quotes WHERE market_key=? ORDER BY received_at',(key,))]
    unique={(q['received_at'],q['midpoint'],q['yes_bid'],q['yes_ask']):q for q in quotes}
    quotes=sorted(unique.values(),key=lambda q:q['received_at'] or '')
    usable=[q for q in quotes if q['status']=='usable' and type(q['midpoint']) in (int,float)]
    tracked=db.execute('SELECT data FROM native_tracking WHERE market_key=? ORDER BY observed_at DESC,rowid DESC LIMIT 1',(key,)).fetchone()
    tracking=json.loads(tracked[0]) if tracked else {}
    venue=db.execute('SELECT * FROM latest_venue WHERE market_key=?',(key,)).fetchone()
    def field(name,fallback): return materials.get(name,fallback) if materials else fallback
    result={'key':key,'venue':market['venue'],'venue_id':market['venue_id'],'url':safe_link(market['url']),
        'title':clean(market['question']),'rules':clean(market['rules']),'created_at':market['created_at'],
        'close_at':task.get('market',{}).get('close_at'),'resolution_hint':task.get('market',{}).get('resolution_hint'),
        'first_seen_at':None,'first_seen_status':'not_recorded','enrolled_at':task.get('enrolled_at'),
        'worker_status':task.get('status','not_recorded'),'effective_model_status':
            'completed_after_supplement' if selected and selected['source_kind']=='material_revision' and completed else
            'completed' if completed else task.get('status','not_recorded'),
        'selected_revision_id':selected['id'] if selected else None,
        'local_material_status':selected['material_status'] if selected else 'pack_not_synced',
        'readable_sources_count':selected.get('source_count') if selected else None,
        'status_observed_at':native['observed_at'] if native else None,
        'executions':task.get('executions'),'retry_at':task.get('retry_at'),'error_type':task.get('error_type'),
        'analysis_stage':task.get('analysis_stage'),'opening_quote':None,'opening_status':'not_recorded',
        'earliest_quote':usable[0] if usable else None,'latest_quote':usable[-1] if usable else None,
        'quote_history':quotes,'quote_history_scope':'indexed_forecast_anchors_and_native_target_window_quotes',
        'prices':price_cells(market,tracking,datetime.now(timezone.utc)),'revisions':versions,
        'sources':field('sources',[]),'searches':field('searches',[]),'fetches':field('fetches',[]),
        'gaps':field('gaps',[]),'plan':field('plan',[]),'graph':field('graph',{}),
        'map_status':field('map_status','local_graph_not_imported'),'evidence':[],
        'scoring_sources':[],'material_frozen_at':field('frozen_at',None),
        'material_sha256':field('material_sha256',None),'graph_sha256':field('graph_sha256',None),
        'result_sha256':selected.get('result_sha256') if selected else None,
        'original_run':json.loads(native['source']).get('run_id') if native else None,
        'venue_status':venue['status'] if venue else 'not_recorded','final_result':venue['result'] if venue else None}
    if with_history: result['history']=read_history(db,key)
    return result


def read_catalog(db,*,with_history=False):
    rows=[read_task(db,r[0],with_history=with_history) for r in db.execute('SELECT key FROM markets ORDER BY created_at DESC,key')]
    def source(view):
        row=db.execute('SELECT source,observed_at FROM '+view+' ORDER BY observed_at DESC LIMIT 1').fetchone()
        return {**json.loads(row['source']),'observed_at':row['observed_at']} if row else {'run_id':None}
    return {'schema':'prediction-market-web-v1','database_contract':'market-web-database-v1',
        'generated_at':clock(),'timezone':'Asia/Shanghai','cohort':'all_enrolled_prediction_markets',
        'tasks':rows,'source_receipts':{'worker':source('latest_tasks'),'monitor':source('latest_discovery')},
        'read_only':True,'new_provider_calls':0,'history_refresh_interval_seconds':7200,
        'watermark':read_watermark(db)}


def read_index(db):
    rows=[read_task(db,r[0],with_history=False,with_materials=False)
          for r in db.execute('SELECT key FROM markets ORDER BY created_at DESC,key')]
    return {'schema':'market-web-index-v1','database_contract':'market-web-database-v1',
        'generated_at':clock(),'watermark':read_watermark(db),'tasks':rows,
        'read_only':True,'new_provider_calls':0}


def read_watermark(db):
    """Stable across polling until underlying saved data or verification changes."""
    value={'last_record_seq':db.execute('SELECT COALESCE(max(seq),0) FROM records').fetchone()[0]}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_packets'").fetchone():
        value['web_verified_at']=db.execute('SELECT max(observed_at) FROM web_packets').fetchone()[0]
    return hashlib.sha256(encoded(value)).hexdigest()


def read_body(db,sha):
    if not __import__('re').fullmatch('[0-9a-f]{64}',sha): raise ValueError('Invalid display hash')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='web_documents'").fetchone():
        raise KeyError('Verified display bodies are not initialized')
    row=db.execute('SELECT text,byte_length FROM web_documents WHERE display_sha256=?',(sha,)).fetchone()
    if row is None: raise KeyError('Verified display body not imported')
    raw=row['text'].encode()
    if len(raw)!=row['byte_length'] or hashlib.sha256(raw).hexdigest()!=sha:
        raise ValueError('Display body integrity differs')
    return raw


def publish_snapshot(db,output):
    """Match the existing static Web filenames with immutable verified bodies/prices."""
    output=Path(output)
    (output/'bodies').mkdir(parents=True,exist_ok=True)
    (output/'prices').mkdir(exist_ok=True)
    data=read_catalog(db,with_history=True)
    for row in db.execute('SELECT display_sha256 FROM web_documents'):
        (output/'bodies'/(row[0]+'.txt')).write_bytes(read_body(db,row[0]))
    index={'schema':'market-web-price-index-v1','generated_at':data['generated_at'],
        'watermark':data['watermark'],'interval_seconds':7200,'markets':{}}
    for task in data['tasks']:
        raw=encoded(task['history']);sha=hashlib.sha256(raw).hexdigest()
        path='prices/'+sha+'.json'
        (output/path).write_bytes(raw)
        index['markets'][task['key']]={'path':path,'sha256':sha,'status':task['history']['status']}
    for name,value in (('market-research.json',data),('market-price-index.json',index)):
        temporary=output/(name+'.next')
        temporary.write_bytes(encoded(value));temporary.replace(output/name)
    return {'markets':len(data['tasks']),'material_batches':sum(len(t['revisions']) for t in data['tasks']),
        'verified_documents':db.execute('SELECT count(*) FROM web_documents').fetchone()[0],
        'watermark':data['watermark'],'models':0,'searches':0}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('hydrate','export'))
    p.add_argument('--root',default='E:/metaculus_data/prediction_markets/store')
    p.add_argument('--object-root',action='append',default=[])
    p.add_argument('--archive-root')
    p.add_argument('--output')
    args=p.parse_args()
    if args.command=='hydrate':
        from .store import connect
        from ForecastAgent.runtime.task_lock import task_lock
        with task_lock(Path(args.root)):
            db=connect(Path(args.root)/'market.sqlite3')
            try: print(json.dumps(hydrate(db,args.root,args.object_root,args.archive_root),ensure_ascii=False))
            finally: db.close()
    else:
        if not args.output: p.error('--output required for export')
        db=open_readonly(args.root)
        try:
            # One WAL read transaction freezes a coherent projection across all queries.
            db.execute('BEGIN')
            output=Path(args.output)
            report=publish_snapshot(db,output)
            db.rollback()
            print(json.dumps({**report,'output':str(output)}))
        finally: db.close()


if __name__=='__main__': main()
