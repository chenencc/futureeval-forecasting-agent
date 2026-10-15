"""SHA-verified, incremental native competition import; no platform/model calls."""
from collections import defaultdict
import hashlib
import gzip
import json
from pathlib import Path, PurePosixPath
import re

from ForecastAgent.market_store.github_io import Archives,BlockFetch
from ForecastAgent.local_transfer import SignedRanges,RangeFile,ArtifactZip
from ForecastAgent.market_store.store import clock,digest,encoded
from ForecastAgent.runtime.task_lock import task_lock
from . import store

WORKFLOWS={'official-worker':'official_competition.yaml','official-monitor':'run_bot_on_tournament.yaml'}

def catalog(archives,stream):
    artifact=archives.latest(WORKFLOWS[stream],'forecast-local-catalog-v1-'+stream)
    if not artifact: raise ValueError('Required official catalog absent: '+stream)
    with archives.open(artifact) as z:
        manifest=json.loads(z.read('sync-manifest.json',16*1024**2));summary=json.loads(z.read('summary.json',32*1024**2))
        if manifest['repo']!=archives.repo or manifest['stream']!=stream or summary['stream']!=stream or str(manifest['run_id'])!=str(artifact['workflow_run']['id']) or summary['run_id']!=manifest['run_id']:
            raise ValueError('Official catalog provenance differs')
        embedded={sha:z.read(name,16*1024**2) for sha,name in manifest.get('embedded',{}).items()}
        if any(hashlib.sha256(raw).hexdigest()!=sha for sha,raw in embedded.items()): raise ValueError('Embedded object hash differs')
    return manifest,summary,embedded

class Native:
    def __init__(self,db,archives,source,embedded,archive_root):
        self.db,self.archives,self.source,self.embedded=db,archives,source,embedded
        self.generic=Path(archive_root)/'blobs/sha256';self.archive=None

    def read(self,entry,local_only=False):
        name=entry['path'];sha=entry['sha256'];path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or str(path)!=name or not re.fullmatch('[0-9a-f]{64}',sha):
            raise ValueError('Unsafe native file reference')
        saved=self.db.execute('SELECT bytes,codec FROM native_objects WHERE sha256=?',(sha,)).fetchone()
        if entry['size_bytes']>64*1024**2: raise FileNotFoundError('Native object exceeds the explicit 64 MiB import bound')
        local=self.generic/sha[:2]/sha
        cache=self.archives.root/'objects'/sha
        if saved: raw=gzip.decompress(saved[0]) if saved[1]=='gzip' else saved[0]
        elif sha in self.embedded: raw=self.embedded[sha]
        elif cache.is_file(): raw=cache.read_bytes()
        elif local.is_file(): raw=local.read_bytes()
        else:
            if local_only: raise FileNotFoundError('Historical inventory object is not synchronized locally')
            if self.archive is None:
                artifact=self.archives.client.api('repos/'+self.archives.repo+'/actions/artifacts/'+str(self.source['artifact_id']))
                if str(artifact['workflow_run']['id'])!=str(self.source['_run_id']): raise ValueError('Native source belongs to another run')
                fetch=SignedRanges(self.archives.client.executable,self.archives.repo,artifact['id'],artifact,self.archives.traffic)
                blocks=BlockFetch(self.archives.root/'blocks'/str(artifact['id']),artifact,fetch,self.archives.traffic)
                self.archive=ArtifactZip(RangeFile(self.archives.root/'downloads'/str(artifact['id']),artifact,blocks,self.archives.traffic))
            raw=self.archive.read(name,64*1024**2)
        if len(raw)!=entry['size_bytes'] or hashlib.sha256(raw).hexdigest()!=sha: raise ValueError('Native file size/hash differs')
        store.save(self.db,'native_objects',(sha,gzip.compress(raw,compresslevel=6,mtime=0),len(raw),'gzip'))
        return json.loads(raw)

    def close(self):
        if self.archive: self.archive.close()

def material_file(path):
    """Import complete task documents and model journals, excluding binary/cache copies."""
    return (path.endswith('.json') and (path in ('campaign.json','report.json','state.json','monitor-health.json','archive-manifest.json') or
        re.search(r'(?:^|/)incoming[^/]*/(?:index|\d+)\.json$',path) or re.fullmatch(r'[^/]+/(?:index|\d+)\.json',path) or
        path.startswith('tasks/') and not any(x in path for x in ('/cache/','/tool_outputs/','/eager-capture/','/eager-final/'))))

def project(db,files,at,source,tournament='fall-futureeval-2026'):
    """Project exact saved files in deterministic order, not frontend exports."""
    campaign=files.get('campaign.json') or {}
    for ident,task in campaign.get('tasks',{}).items(): store.task(db,ident,task,at,source)
    indexed={str(q['question_id']) for p,value in files.items() if p.endswith('/index.json') for q in value.get('questions',[])}
    indexed.update(campaign.get('tasks',{}))
    for path,value in files.items():
        if not isinstance(value,dict): continue
        post=value.get('post')
        if post and isinstance(post,dict):
            for q in ([post['question']] if post.get('question') else [])+(post.get('group_of_questions') or {}).get('questions',[]):
                if indexed and str(q['id']) not in indexed: continue
                desc={**q,'post_id':post['id'],'title':q.get('title') or post.get('title'),
                    'resolution_criteria':q.get('resolution_criteria') or post.get('resolution_criteria'),
                    'fine_print':q.get('fine_print') or post.get('fine_print'),
                    'url':'https://www.metaculus.com/questions/'+str(post['id'])+'/'}
                store.question(db,desc,tournament,value.get('retrieved_at_utc') or at,source)
    grouped=defaultdict(dict)
    for path,value in files.items():
        m=re.fullmatch(r'tasks/(\d+)/(.*)',path)
        if m: grouped[m[1]][m[2]]=value
    for ident,objects in grouped.items():
        key=store.identify(db,ident);task=campaign.get('tasks',{}).get(ident,{})
        batches={}
        for path,value in objects.items():
            if not isinstance(value,dict): continue
            if path.endswith(('analysis-input.json','package.json','bundle.json')) and isinstance(value.get('request'),dict) and isinstance(value.get('pages'),dict):
                if str(value['request'].get('id'))!=ident: raise ValueError('Task material belongs to another question')
                batches[path]=store.bundle(db,ident,value,path,at,source)
                if not db.execute('SELECT 1 FROM question_versions WHERE question_key=?',(key,)).fetchone():
                    store.question(db,{**value['request'],'post_id':task.get('post_id')},tournament,value.get('request',{}).get('as_of_utc') or at,source)
            if path.endswith('/request.json') or '/http/' in path or '/model_calls/' in path:
                store.event(db,key,'model_record',value,sha=digest(value))
        candidate=objects.get('candidate.json')
        if candidate:
            fid=store.candidate(db,ident,candidate,at,source)
            store.bind_input(db,ident,fid,batches.get('analysis-input.json'),objects,at)
        receipt=objects.get('submission.json')
        if receipt: store.receipt(db,ident,receipt,at,source)
    return len(grouped)

def import_catalog(db,archives,stream,archive_root):
    manifest,summary,embedded=catalog(archives,stream)
    source_id=stream+':'+str(manifest['run_id']);at=summary['generated_at']
    previous=db.execute('SELECT data FROM imports WHERE id=?',(source_id,)).fetchone()
    if previous:
        if json.loads(previous[0])['manifest_sha256']!=digest(manifest): raise ValueError('Previously imported catalog identity changed')
        return {'stream':stream,'run_id':manifest['run_id'],'status':'already_imported'}
    grouped={};failures=[];refs=0
    with db:
        # A checkpoint contains controls; its paired evidence source contains new
        # full documents. Merge only within this exact run and refuse conflicts.
        for source in manifest['sources']:
            source={**source,'_run_id':manifest['run_id']}
            reader=Native(db,archives,source,embedded,archive_root)
            try:
                for entry in source['files']:
                    path=entry['path'];match=re.match(r'tasks/(\d+)/',path)
                    question_key=store.identify(db,match[1]) if match else None
                    value=None;availability='reference_only'
                    if material_file(path):
                        try: value=reader.read(entry);availability='verified_local_object'
                        except FileNotFoundError: failures.append({'path':path,'reason':'native_file_not_available'});availability='missing'
                    ref={**entry,'source':{'repo':manifest['repo'],'run_id':manifest['run_id'],'artifact_id':source['artifact_id'],'artifact_name':source['artifact_name']}}
                    store.save(db,'file_refs',(digest(ref),source_id,question_key,path,entry['sha256'],entry['size_bytes'],availability,encoded(ref).decode()));refs+=1
                    if value is not None:
                        if path in grouped and grouped[path]!=value: raise ValueError('Same-run native file conflict')
                        grouped[path]=value
            finally: reader.close()
        # The sealed official inventory points to older original evidence that
        # was moved out of the hot checkpoint. Reuse verified local SHA objects;
        # a missing historical body remains an import gap, never a new capture.
        inventory=grouped.get('archive-manifest.json',{}).get('files',{})
        reader=Native(db,archives,{**manifest['sources'][0],'_run_id':manifest['run_id']},embedded,archive_root)
        for path,entry in inventory.items():
            if path in grouped or not material_file(path): continue
            entry={'path':path,**entry};match=re.match(r'tasks/(\d+)/',path)
            key=store.identify(db,match[1]) if match else None
            try: value=reader.read(entry,local_only=True);availability='verified_local_object';grouped[path]=value
            except FileNotFoundError: availability='missing';failures.append({'path':path,'reason':'historical_native_object_not_local'})
            ref={**entry,'inventory_source':source_id,'role':'historical_inventory_local'}
            store.save(db,'file_refs',(digest(ref),source_id,key,path,entry['sha256'],entry['size_bytes'],availability,encoded(ref).decode()));refs+=1
        project(db,grouped,at,source_id)
        receipt={'manifest_sha256':digest(manifest),'summary':summary,'manifest':manifest,'failures':failures}
        store.save(db,'imports',(source_id,stream,str(manifest['run_id']),at,clock(),encoded(receipt).decode()))
    return {'stream':stream,'run_id':manifest['run_id'],'files':refs,'missing':len(failures),'status':'imported'}

def repair_missing_local(db, archives, archive_root):
    """Revisit prior import gaps when their original SHA objects arrive later."""
    rows=list(db.execute("SELECT * FROM file_refs WHERE availability='missing' ORDER BY source_id,path"))
    repaired=[];by_source=defaultdict(list)
    reader=Native(db,archives,{}, {},archive_root)
    for row in rows:
        try:
            value=reader.read({'path':row['path'],'sha256':row['sha256'],'size_bytes':row['size_bytes']},local_only=True)
        except FileNotFoundError:
            continue
        by_source[row['source_id']].append((row,value))
    db.execute('''CREATE TABLE IF NOT EXISTS native_recovery_receipts(
        id TEXT PRIMARY KEY,ref_id TEXT NOT NULL,sha256 TEXT NOT NULL,recovered_at TEXT NOT NULL)''')
    for source, values in by_source.items():
        original=db.execute('SELECT observed_at FROM imports WHERE id=?',(source,)).fetchone()
        if original is None:raise ValueError('Recovery source import receipt missing')
        files={row['path']:value for row,value in values}
        keys={row['question_key'] for row,_ in values}
        for row in db.execute('SELECT * FROM file_refs WHERE source_id=?',(source,)).fetchall():
            if row['question_key'] not in keys or row['path'] in files or not material_file(row['path']):continue
            try: files[row['path']]=reader.read({'path':row['path'],'sha256':row['sha256'],'size_bytes':row['size_bytes']},local_only=True)
            except FileNotFoundError:continue
        with db:
            project(db,files,original[0],source)
            for row,_ in values:
                db.execute("UPDATE file_refs SET availability='verified_local_object' WHERE id=?",(row['id'],))
                db.execute('INSERT OR IGNORE INTO native_recovery_receipts VALUES(?,?,?,?)',
                           (digest([row['id'],row['sha256']]),row['id'],row['sha256'],clock()))
                repaired.append(row['id'])
    return {'repaired_references':len(repaired),'remaining_missing':db.execute(
        "SELECT count(*) FROM file_refs WHERE availability='missing'").fetchone()[0],
        'recaptured':False,'original_observation_clocks_preserved':True}


def sync(root,archives=None,archive_root='E:/metaculus_data'):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with task_lock(root):
        db=store.connect(root/'competition.sqlite3')
        report={'started_at':clock(),'models':0,'searches':0,'submissions':0,'imports':[]}
        try:
            archives=archives or Archives(root/'github-cache')
            for stream in ('official-monitor','official-worker'):report['imports'].append(import_catalog(db,archives,stream,archive_root))
            report['recovery']=repair_missing_local(db,archives,archive_root)
            from .local import market_pulse
            report['local_market_pulse']=market_pulse(db,archive_root)
            from ForecastAgent import pipeline_readiness,pipeline_health
            replay=pipeline_readiness.competition(db)
            pipeline_readiness.persist(db,replay)
            report['replay']={k:v for k,v in replay.items() if k!='tasks'}
            pipeline_health.persist(db,pipeline_health.alerts(db,'competition')+pipeline_health.replay_alerts(replay,'competition'))
            report.update(status='ok',finished_at=clock(),stats=store.stats(db))
        except Exception as exc:
            report.update(status='failed',finished_at=clock(),error_type=type(exc).__name__,reason=str(exc)[:200]);raise
        finally:
            (root/'latest-sync.json').write_bytes(encoded(report));db.close()
    return report
