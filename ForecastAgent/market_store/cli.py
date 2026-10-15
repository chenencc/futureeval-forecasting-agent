"""Cloud collection and local replication; public data only, no forecast execution."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from ForecastAgent.local_sync import task_lock
from .store import SCHEMA,append,clock,connect,encoded,records_since,stats
from .github_io import Archives,HISTORY_ARTIFACT,HISTORY_WORKFLOW,MAX_DATABASE,bounded_gunzip,import_catalog
from .collector import collect


def write(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(encoded(value))


def restore(archives,root,artifact=None):
    artifact=artifact or archives.latest(HISTORY_WORKFLOW,HISTORY_ARTIFACT)
    if not artifact: return None
    with archives.open(artifact) as archive:
        manifest=json.loads(archive.read('manifest.json',1024**2))
        if (manifest.get('schema')!=SCHEMA or manifest.get('repo')!=archives.repo or
            str(manifest['run_id'])!=str(artifact['workflow_run']['id'])):
            raise ValueError('History checkpoint provenance mismatch')
        raw=bounded_gunzip(archive.read('market.sqlite3.gz',MAX_DATABASE))
        if hashlib.sha256(raw).hexdigest()!=manifest['database_sha256']: raise ValueError('History DB hash mismatch')
    path=Path(root)/'market.sqlite3'
    if path.exists(): raise ValueError('Restore target exists')
    path.write_bytes(raw)
    db=connect(path)
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('SQLite checkpoint integrity failed')
    db.close()
    return {'artifact_id':str(artifact['id']),'run_id':manifest['run_id'],
            'last_seq':manifest['last_seq'],'database_sha256':manifest['database_sha256']}


def cloud(args):
    root=Path(args.root)
    root.mkdir(parents=True,exist_ok=True)
    output=root/'publication'
    output.mkdir(exist_ok=True)
    archives=Archives(root/'github-cache',branch=args.branch)
    parent=restore(archives,root)
    db=connect(root/'market.sqlite3')
    start=db.execute('SELECT COALESCE(max(seq),0) FROM records').fetchone()[0]
    report={'schema':SCHEMA,'run_id':os.environ['GITHUB_RUN_ID'],'parent':parent,'catalogs':[],
            'mode':'public_history_only','models_or_searches_called':False,'started_at':clock()}
    success=False
    try:
        for stream in ('market-worker','market-monitor'):
            report['catalogs'].append(import_catalog(db,archives,stream))
        report['collection']=collect(db,output/'responses',report['run_id'],max_requests=args.max_requests,
            seconds=args.seconds,max_markets=args.max_markets)
        success=True
    except Exception as exc:
        report['failure']={'type':type(exc).__name__,'reason':str(exc)[:200]}
        raise
    finally:
        report.update(finished_at=clock(),stats=stats(db),collection_finished=success)
        with gzip.open(output/'delta.jsonl.gz','wb',compresslevel=6) as stream:
            for record in records_since(db,start): stream.write(encoded(record)+b'\n')
        temporary=root/'backup.sqlite3'
        dest=sqlite3.connect(temporary)
        db.backup(dest)
        # The cloud checkpoint is a bounded hot cache, not a growing duplicate
        # of the local research database. Immutable deltas carry the full history.
        compact_checkpoint(dest)
        dest.close()
        raw=temporary.read_bytes()
        (output/'market.sqlite3.gz').write_bytes(gzip.compress(raw,compresslevel=6,mtime=0))
        last=db.execute('SELECT COALESCE(max(seq),0) FROM records').fetchone()[0]
        files={p.relative_to(output).as_posix():{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
            'size':p.stat().st_size} for p in output.rglob('*') if p.is_file()}
        write(output/'report.json',report)
        write(output/'manifest.json',{'schema':SCHEMA,'repo':archives.repo,'run_id':report['run_id'],
            'parent':parent,'first_seq':start+1,'last_seq':last,'database_sha256':hashlib.sha256(raw).hexdigest(),
            'files':files,'generated_at':clock(),'checkpoint_scope':'control_and_overlap_cache',
            'delta_role':'durable_history_records_for_local_import','retention_days':7})
        db.close()
    print(json.dumps(report,ensure_ascii=False))


def compact_checkpoint(db):
    """Retain exact cursors and only the price overlap needed next dispatch."""
    with db:
        db.execute('DELETE FROM price_points WHERE NOT EXISTS (SELECT 1 FROM cursors c '
            'WHERE c.market_key=price_points.market_key AND ((point_at BETWEEN c.scanned_until-7200 AND c.scanned_until) '
            'OR point_at>=COALESCE(json_extract(c.data,\'$.live_scanned_until\')-7200,c.scanned_until-7200)))')
        db.execute('DELETE FROM records WHERE kind=\'point\' AND id NOT IN (SELECT id FROM price_points)')
        db.execute('DELETE FROM history_windows')
        db.execute('DELETE FROM records WHERE kind=\'window\'')
        db.execute('DELETE FROM task_observations WHERE id NOT IN (SELECT o.id FROM task_observations o WHERE o.rowid=('
            'SELECT x.rowid FROM task_observations x WHERE x.market_key=o.market_key AND '
            'COALESCE(json_extract(x.source,\'$.stream\'),\'\')=COALESCE(json_extract(o.source,\'$.stream\'),\'\') '
            'ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1))')
        db.execute('DELETE FROM records WHERE kind=\'task\' AND id NOT IN (SELECT id FROM task_observations)')
        db.execute('DELETE FROM records WHERE kind=\'cursor\' AND rowid NOT IN (SELECT max(rowid) FROM records '
            'WHERE kind=\'cursor\' GROUP BY market_key)')
        db.execute('DELETE FROM native_tracking WHERE rowid NOT IN (SELECT max(rowid) FROM native_tracking GROUP BY market_key)')
        db.execute('DELETE FROM records WHERE kind=\'tracking\' AND id NOT IN (SELECT id FROM native_tracking)')
        db.execute('DELETE FROM venue_observations WHERE id NOT IN (SELECT id FROM latest_venue)')
        db.execute('DELETE FROM records WHERE kind=\'venue\' AND id NOT IN (SELECT id FROM venue_observations)')
        db.execute('DELETE FROM pack_locations WHERE rowid NOT IN (SELECT max(rowid) FROM pack_locations GROUP BY market_key,sha256)')
        db.execute('DELETE FROM records WHERE kind=\'pack\' AND id NOT IN (SELECT id FROM pack_locations)')
    db.execute('VACUUM')


def mirror(args):
    root=Path(args.root)
    root.mkdir(parents=True,exist_ok=True)
    with task_lock(root):
        archives=Archives(root/'github-cache')
        db=connect(root/'market.sqlite3')
        report={'started_at':clock(),'database':str(root/'market.sqlite3'),'catalogs':[],'history':[]}
        try:
            for stream in ('market-worker','market-monitor'):
                report['catalogs'].append(import_catalog(db,archives,stream))
            artifacts,gaps=archives.pending_history(db)
            previous=db.execute("SELECT value FROM metadata WHERE key='history_publication_gaps'").fetchone()
            known={encoded(x).decode():x for x in ([*json.loads(previous[0]),*gaps] if previous else gaps)}
            report['history_gaps']=list(known.values())
            with db:
                db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('history_publication_gaps',encoded(report['history_gaps']).decode()))
            # Bootstrap control/payload dependencies, not a fabricated full price history.
            if artifacts and (gaps or not db.execute("SELECT 1 FROM imports WHERE source_id LIKE 'history-run:%'").fetchone()):
                with tempfile.TemporaryDirectory(dir=root) as folder:
                    restore(archives,folder,artifacts[-1])
                    source=connect(Path(folder)/'market.sqlite3')
                    with db:
                        for record in records_since(source,0):
                            append(db,record['kind'],record['key'],record['data'],record['observed_at'],ident=record['id'])
                    source.close()
            for artifact in artifacts:
                with archives.open(artifact) as archive:
                    manifest=json.loads(archive.read('manifest.json',1024**2))
                    if (manifest.get('schema')!=SCHEMA or manifest.get('repo')!=archives.repo or
                        str(manifest['run_id'])!=str(artifact['workflow_run']['id'])):
                        raise ValueError('History manifest provenance differs')
                    raw=archive.read('delta.jsonl.gz',MAX_DATABASE)
                    if hashlib.sha256(raw).hexdigest()!=manifest['files']['delta.jsonl.gz']['sha256']:
                        raise ValueError('Incremental records hash differs')
                    records=[json.loads(line) for line in bounded_gunzip(raw).splitlines() if line]
                    added=0
                    with db:
                        for record in records:
                            added+=append(db,record['kind'],record['key'],record['data'],record['observed_at'],ident=record['id'])
                    item_report={'artifact_id':str(artifact['id']),'run_id':manifest['run_id'],
                        'new_records':added,'mode':'incremental_delta'}
                    # Public responses are persisted before acknowledging this import.
                    count=0
                    for name,item in manifest['files'].items():
                        if not name.startswith('responses/'): continue
                        if name!='responses/'+item['sha256']: raise ValueError('Unsafe response object name')
                        path=root/'objects'/item['sha256'][:2]/item['sha256']
                        if path.exists():
                            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']: raise ValueError('Cached response hash differs')
                            continue
                        raw=archive.read(name,16*1024**2)
                        if len(raw)!=item['size'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:
                            raise ValueError('Response object hash differs')
                        path.parent.mkdir(parents=True,exist_ok=True)
                        path.write_bytes(raw)
                        count+=1
                    item_report['new_response_objects']=count
                    with db:
                        db.execute('INSERT INTO imports VALUES(?,?,?)',
                            ('history-run:'+str(manifest['run_id']),clock(),manifest['database_sha256']))
                    report['history'].append(item_report)
            from .local_revisions import import_revisions
            report['local_material_revisions']=import_revisions(db,root.parent)
            policy=root/'web-material-policy.json'
            if policy.is_file():
                settings=json.loads(policy.read_bytes())
                if settings.get('enabled') is True:
                    from .web_materials import hydrate
                    result=hydrate(db,root,settings.get('object_roots',[]))
                    report['web_materials']={'packets':len(result['packets']),'new_documents':result['new_documents'],
                        'states':{state:sum(p['status']==state for p in result['packets'])
                            for state in sorted({p['status'] for p in result['packets']})}}
            competition_policy=root/'competition-policy.json'
            if competition_policy.is_file():
                settings=json.loads(competition_policy.read_bytes())
                if settings.get('enabled') is True:
                    from ForecastAgent.competition_store.ingest import sync as competition_sync
                    competition_root=Path(settings.get('root') or root.parent.parent/'competitions/store')
                    try:
                        result=competition_sync(competition_root,archive_root=root.parent.parent)
                        report['competition']={k:result[k] for k in ('status','finished_at','stats')}
                    except Exception as exc:
                        # Separate mirror failures do not invalidate an already
                        # completed market import. Each database has its own health.
                        report['competition']={'status':'failed','error_type':type(exc).__name__,'reason':str(exc)[:200]}
            report.update(finished_at=clock(),stats=stats(db),status='ok')
            write(root/'latest-sync.json',report)
        except Exception as exc:
            report.update(finished_at=clock(),status='failed',error_type=type(exc).__name__,reason=str(exc)[:200])
            write(root/'latest-sync.json',report)
            raise
        finally:
            db.close()
    print(json.dumps(report,ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('collect')
    p.add_argument('--root',required=True)
    p.add_argument('--branch',default='main')
    p.add_argument('--max-requests',type=int,default=600)
    p.add_argument('--max-markets',type=int,default=1000)
    p.add_argument('--seconds',type=int,default=1200)
    p=sub.add_parser('sync')
    p.add_argument('--root',default='E:/metaculus_data/prediction_markets/store')
    args=parser.parse_args()
    if args.command=='collect':
        if not 1<=args.max_requests<=3000 or not 1<=args.max_markets<=10000 or not 1<=args.seconds<=1800:
            parser.error('History request, market or time bound invalid')
        cloud(args)
    else: mirror(args)


if __name__=='__main__': main()
