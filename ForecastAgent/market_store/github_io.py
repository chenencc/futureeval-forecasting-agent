"""Read compact trusted Actions members without downloading evidence ZIPs."""
import gzip
import hashlib
import io
import json
import lzma
from pathlib import Path
import re

from ForecastAgent.local_sync import GitHub,GitHubReadError
from ForecastAgent.local_transfer import TrafficBudget
from .store import append,digest,encoded,ingest_summary

REPO='chenencc/futureeval-forecasting-agent'
HISTORY_WORKFLOW='market_price_history.yaml'
HISTORY_ARTIFACT='market-price-history-v1'
CATALOGS={'market-worker':'market_research_worker.yaml','market-monitor':'market_research_monitor.yaml'}
MAX_DATABASE=512*1024**2


class Archives:
    def __init__(self,root,repo=REPO,client=None,branch='main'):
        self.root=Path(root)
        self.repo,self.client,self.branch=repo,client or GitHub(),branch
        self.traffic=TrafficBudget(self.root,0,0)

    def latest(self,workflow,name):
        try:
            runs=self.client.api(f'repos/{self.repo}/actions/workflows/{workflow}/runs?branch={self.branch}&status=completed&per_page=30')['workflow_runs']
        except GitHubReadError as exc:
            if exc.status!=404: raise
            self.client.api('repos/'+self.repo)
            return None
        for run in runs:
            if (run['head_branch']!=self.branch or run.get('head_repository',{}).get('full_name')!=self.repo or
                run['path']!='.github/workflows/'+workflow):
                raise ValueError('Untrusted workflow identity')
            if str(run['id'])==__import__('os').environ.get('GITHUB_RUN_ID'): continue
            artifacts=self.client.api(f'repos/{self.repo}/actions/runs/{run["id"]}/artifacts?per_page=100')['artifacts']
            hits=[a for a in artifacts if a['name']==name]
            if len(hits)>1: raise ValueError('Ambiguous artifact')
            if hits:
                if hits[0]['expired']: raise ValueError('Latest checkpoint expired; refuse silent bootstrap')
                return hits[0]
        if len(runs)==30:
            raise ValueError('Checkpoint lookup horizon reached')
        return None

    def open(self,artifact):
        return self.client.open_zip(self.root,self.repo,artifact,self.traffic)

    def read(self,artifact,name,maximum=32*1024**2):
        with self.open(artifact) as archive: return archive.read(name,maximum)

    def pending_history(self,db):
        """Return unimported deltas oldest-first, including preserved failed runs."""
        result,gaps=[],[]
        try:
            runs=self.client.api(f'repos/{self.repo}/actions/workflows/{HISTORY_WORKFLOW}/runs?branch={self.branch}&status=completed&per_page=100')['workflow_runs']
        except GitHubReadError as exc:
            if exc.status!=404: raise
            self.client.api('repos/'+self.repo)
            return [],[]
        # Seven-day artifact retention yields at most 84 scheduled runs. More than
        # 100 missed runs is explicit, never silently declared a complete import.
        for run in runs:
            if (run['head_branch']!=self.branch or run.get('head_repository',{}).get('full_name')!=self.repo or
                run['path']!='.github/workflows/'+HISTORY_WORKFLOW):
                raise ValueError('Untrusted history workflow')
            if db.execute('SELECT 1 FROM imports WHERE source_id=?',('history-run:'+str(run['id']),)).fetchone(): break
            artifacts=self.client.api(f'repos/{self.repo}/actions/runs/{run["id"]}/artifacts?per_page=100')['artifacts']
            hits=[a for a in artifacts if a['name']==HISTORY_ARTIFACT]
            if len(hits)>1: raise ValueError('Ambiguous history publication')
            if hits and not hits[0]['expired']: result.append(hits[0])
            else: gaps.append({'run_id':str(run['id']),'reason':'expired_or_missing_history_artifact'})
        if len(runs)==100 and not any(db.execute('SELECT 1 FROM imports WHERE source_id=?',
            ('history-run:'+str(r['id']),)).fetchone() for r in runs):
            gaps.append({'reason':'history_lookup_horizon_100_runs_reached'})
        return list(reversed(result)),gaps


def load_catalog(archives,stream):
    artifact=archives.latest(CATALOGS[stream],'forecast-local-catalog-v1-'+stream)
    if not artifact: raise ValueError('Market catalog not yet published: '+stream)
    with archives.open(artifact) as archive:
        manifest=json.loads(archive.read('sync-manifest.json',16*1024**2))
        summary=json.loads(archive.read('summary.json',32*1024**2))
        if (manifest['repo']!=archives.repo or manifest['stream']!=stream or summary['stream']!=stream or
            str(manifest['run_id'])!=str(artifact['workflow_run']['id']) or summary['run_id']!=manifest['run_id']):
            raise ValueError('Catalog provenance differs from exact artifact')
        embedded={sha:archive.read(path,16*1024**2) for sha,path in manifest.get('embedded',{}).items()}
        if any(hashlib.sha256(raw).hexdigest()!=sha for sha,raw in embedded.items()):
            raise ValueError('Embedded file hash differs')
    return artifact,manifest,summary,embedded


def original_state(archives,source,embedded):
    """Bootstrap identities from exact state chunks only, never task body packs."""
    files={f['path']:f for f in source['files']}
    meta=files.get('state-pack-manifest.json')
    if meta:
        manifest=pack_manifest(archives,source,embedded)
        original=manifest['files']['state.json']
        artifact=archives.client.api(f'repos/{archives.repo}/actions/artifacts/{source["artifact_id"]}')
        with archives.open(artifact) as archive:
            direct=files.get('state.json')
            if direct: raw=archive.read('state.json',MAX_DATABASE)
            else:
                control=manifest['encoded_controls']['state.json']
                chunks=[]
                for part in control['chunks']:
                    packed=archive.read(part['path'],128*1024**2)
                    if hashlib.sha256(packed).hexdigest()!=part['sha256']: raise ValueError('Control packed hash differs')
                    with lzma.open(io.BytesIO(packed)) as stream: value=stream.read(16*1024**2+1)
                    if len(value)!=part['original_size'] or hashlib.sha256(value).hexdigest()!=part['original_sha256']:
                        raise ValueError('Control original hash differs')
                    chunks.append(value)
                raw=b''.join(chunks)
        if len(raw)!=original['size'] or hashlib.sha256(raw).hexdigest()!=original['sha256']:
            raise ValueError('Frozen state hash differs')
    else:
        meta=files['state.json']
        raw=embedded.get(meta['sha256'])
        if raw is None:
            artifact=archives.client.api(f'repos/{archives.repo}/actions/artifacts/{source["artifact_id"]}')
            raw=archives.read(artifact,'state.json',MAX_DATABASE)
        if hashlib.sha256(raw).hexdigest()!=meta['sha256']: raise ValueError('State hash differs')
    return json.loads(raw)


def pack_manifest(archives,source,embedded):
    meta=next(f for f in source['files'] if f['path']=='state-pack-manifest.json')
    raw=embedded.get(meta['sha256'])
    cache=archives.root/'objects'/meta['sha256']
    if raw is None and cache.exists(): raw=cache.read_bytes()
    if raw is None:
        artifact=archives.client.api(f'repos/{archives.repo}/actions/artifacts/{source["artifact_id"]}')
        raw=archives.read(artifact,'state-pack-manifest.json',32*1024**2)
    if len(raw)!=meta['size_bytes'] or hashlib.sha256(raw).hexdigest()!=meta['sha256']:
        raise ValueError('Original file index hash differs')
    if not cache.exists():
        cache.parent.mkdir(parents=True,exist_ok=True)
        cache.write_bytes(raw)
    return json.loads(raw)


def import_catalog(db,archives,stream):
    artifact,manifest,summary,embedded=load_catalog(archives,stream)
    source_receipt={'repo':archives.repo,'run_id':manifest['run_id'],'artifact_id':str(artifact['id']),'stream':stream}
    # Older publishers omitted outcome IDs. Upgrade only from the same exact hashed original.
    for source in manifest['sources']:
        tasks=summary['sources'][source['artifact_name']].get('state.json',{}).get('tasks',[])
        for task in tasks:
            # Worker and monitor share the same enrollment identity. Reuse already
            # verified fields rather than fetching a large legacy monitor ledger.
            saved=db.execute('SELECT * FROM markets WHERE key=?',(task['id'],)).fetchone()
            if saved:
                for field in ('family','yes_token','condition_id'):
                    if field not in task['market']: task['market'][field]=saved[field]
        if any(not t['market'].get('family') or (t['market']['venue']=='polymarket' and not t['market'].get('yes_token')) for t in tasks):
            state=original_state(archives,source,embedded)
            for task in tasks:
                original=state['tasks'][task['id']]['market']
                if any(original.get(k)!=v for k,v in task['market'].items()):
                    raise ValueError('Projected and original market differ')
                task['market']={k:v for k,v in original.items() if k!='screen_text'}
    with db:
        ingest_summary(db,summary,source_receipt)
        keys={r[0] for r in db.execute('SELECT key FROM markets')}
        directories={digest(k):k for k in keys}
        for source in manifest['sources']:
            new_packs=[]
            for file in source['files']:
                match=re.fullmatch(r'packs/([0-9a-f]{64})\.tar\.xz',file['path'])
                if not match or match[1] not in directories: continue
                key=directories[match[1]]
                data={**file,'source':{**source_receipt,'state_artifact_id':source.get('artifact_id')}}
                append(db,'pack',key,data,summary['generated_at'])
                if not db.execute('SELECT 1 FROM metadata WHERE key=?',('refs:'+file['sha256'],)).fetchone():
                    new_packs.append(file)
            if not new_packs: continue
            for file in source['files']:
                if file['path']!='state-pack-manifest.json': continue
                packed=pack_manifest(archives,source,embedded)
                wanted={Path(p['path']).stem.split('.')[0] for p in new_packs}
                for path,value in packed['files'].items():
                    match=re.match(r'^tasks/([0-9a-f]{64})/',path)
                    if not match or match[1] not in directories or match[1] not in wanted: continue
                    data={'path':path,'sha256':value['sha256'],'size_bytes':value['size'],
                          'source':{**source_receipt,'state_artifact_id':source.get('artifact_id')}}
                    key=directories[match[1]]
                    token=digest({'kind':'ref','key':key,'path':path,'sha256':value['sha256']})
                    if not db.execute('SELECT 1 FROM records WHERE id=?',(token,)).fetchone():
                        append(db,'ref',key,data,summary['generated_at'],ident=token)
                for item in new_packs:
                    db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',('refs:'+item['sha256'],source_receipt['run_id']))
    return source_receipt


def bounded_gunzip(raw,maximum=MAX_DATABASE):
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream: data=stream.read(maximum+1)
    if len(data)>maximum: raise ValueError('Compressed checkpoint exceeds expansion bound')
    return data
