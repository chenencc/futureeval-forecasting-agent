"""Recover only exact previously saved bytes; never recapture historical evidence."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import time
from datetime import timedelta
from urllib.parse import quote

from ForecastAgent.market_store.github_io import Archives,BlockFetch
from ForecastAgent.market_store.store import clock
from ForecastAgent.local_transfer import atomic_json,SignedRanges,RangeFile,ArtifactZip


class RecoveryArchives(Archives):
    """A time slice ends between cached range blocks, retaining partial bytes."""
    def __init__(self,root,deadline):
        super().__init__(root)
        self.deadline=deadline

    def open(self,artifact):
        source=SignedRanges(self.client.executable,self.repo,artifact['id'],artifact,self.traffic)
        def bounded(start,stop):
            if time.monotonic()>=self.deadline:raise TimeoutError('Recovery time slice exhausted')
            return source(start,stop)
        blocks=BlockFetch(self.root/'blocks'/str(artifact['id']),artifact,bounded,self.traffic)
        return ArtifactZip(RangeFile(self.root/'downloads'/str(artifact['id']),artifact,blocks,self.traffic))


def save_blob(root, raw, expected, size):
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Recovery object size/hash differs')
    path = Path(root)/'blobs/sha256'/expected[:2]/expected
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Existing recovery object corrupt')
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(raw)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def safe_path(path):
    value = PurePosixPath(path)
    if value.is_absolute() or '..' in value.parts or '\\' in path or ':' in path or str(value) != path:
        raise ValueError('Unsafe recovery member')
    return path


def recover(root, *, archives=None, max_bytes=256*1024**2, max_archives=12, market_keys=None,max_seconds=120):
    """Bound transfers, preserve exact locations and keep unresolved gaps explicit."""
    root = Path(root)
    deadline=time.monotonic()+max_seconds
    archives = archives or RecoveryArchives(root/'recovery-cache',deadline)
    report = {'started_at': clock(), 'mode': 'saved_originals_only', 'recovered': [], 'unavailable': [],
              'errors': [], 'max_uncompressed_bytes': max_bytes, 'recovered_bytes': 0,'attempted_uncompressed_bytes':0,
              'models': 0, 'searches': 0, 'recaptures': 0, 'forecasts': 0}
    market = sqlite3.connect((root/'prediction_markets/store/market.sqlite3').as_uri()+'?mode=ro', uri=True)
    market.row_factory = sqlite3.Row
    try:
        from ForecastAgent.market_store.web_read import read_task
        for row in market.execute('SELECT key FROM markets ORDER BY key').fetchall():
            if time.monotonic()>=deadline:break
            key = row[0]
            if market_keys is not None and key not in market_keys:
                continue
            if read_task(market, key, with_history=False)['local_material_status'] != 'pack_not_synced':
                continue
            pack = market.execute('SELECT * FROM pack_locations WHERE market_key=? '
                                  'ORDER BY observed_at DESC,rowid DESC LIMIT 1', (key,)).fetchone()
            if not pack:
                report['unavailable'].append({'key': key, 'reason': 'pack_location_missing'})
                continue
            item = json.loads(pack['data']); source = item['source']
            if source.get('repo') != archives.repo:
                raise ValueError('Recovery pack repository differs')
            path = safe_path(pack['path']); size = item['size_bytes']
            from ForecastAgent.market_store.store import digest
            if path!='packs/'+digest(key)+'.tar.xz':raise ValueError('Recovery pack market binding differs')
            if size > 128*1024**2 or size+report['attempted_uncompressed_bytes'] > max_bytes:
                report['unavailable'].append({'key': key, 'reason': 'recovery_byte_bound'})
                continue
            target = root/'blobs/sha256'/pack['sha256'][:2]/pack['sha256']
            if target.exists() and target.stat().st_size == size and hashlib.sha256(target.read_bytes()).hexdigest() == pack['sha256']:
                continue
            try:
                artifact = archives.client.api(f'repos/{archives.repo}/actions/artifacts/{source["state_artifact_id"]}')
                if str(artifact['workflow_run']['id']) != str(source['run_id']) or artifact.get('expired'):
                    raise ValueError('Recovery pack source run differs or expired')
                report['attempted_uncompressed_bytes']+=size
                raw = archives.read(artifact, path, 128*1024**2)
                save_blob(root, raw, pack['sha256'], size)
                report['recovered_bytes'] += size
                report['recovered'].append({'key': key, 'path': path, 'sha256': pack['sha256'],
                                            'artifact_id': str(artifact['id']), 'run_id': str(source['run_id'])})
                atomic_json(root/'recovery-progress.json',report)
            except Exception as exc:
                report['errors'].append({'key': key, 'error_type': type(exc).__name__, 'reason': str(exc)[:180]})
    finally:
        market.close()
    competition = sqlite3.connect((root/'competitions/store/competition.sqlite3').as_uri()+'?mode=ro', uri=True)
    wanted = {(r[0],r[1]): {'path': safe_path(r[0]), 'sha256': r[1], 'size': r[2]} for r in competition.execute(
        "SELECT DISTINCT path,sha256,size_bytes FROM file_refs WHERE availability='missing'")}
    windows=[]
    from ForecastAgent.pipeline_readiness import instant
    for row in competition.execute('SELECT question_key,data FROM latest_tasks'):
        if not any(path.startswith('tasks/'+row[0].split(':',1)[1]+'/') for path,_ in wanted):continue
        task=json.loads(row[1]);start=instant(task.get('discovered_at_utc'))
        end=instant(task.get('accepted_at_utc'))
        if start and end:windows.append((start-timedelta(minutes=15),end+timedelta(minutes=15)))
    competition.close()
    for ident,item in list(wanted.items()):
        saved=root/'blobs/sha256'/item['sha256'][:2]/item['sha256']
        if saved.is_file() and saved.stat().st_size==item['size'] and hashlib.sha256(saved.read_bytes()).hexdigest()==item['sha256']:
            del wanted[ident]
    # Inventory paths can have multiple historical hashes. Never silently choose
    # a conflicting version; different bytes are retained under different hashes.
    # The original discovery/submission clocks locate older evidence directly.
    # A bounded scan of only the newest runs would starve older gaps forever.
    merged=[]
    for start,end in sorted(windows):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    candidates=[];seen_runs=set()
    for start,end in merged[:12] if wanted else []:
        selected=quote(start.isoformat()+'..'+end.isoformat(),safe='')
        runs=archives.client.api(f'repos/{archives.repo}/actions/workflows/official_competition.yaml/runs?branch=main&per_page=30&created={selected}')['workflow_runs']
        for run in runs:
            if run['id'] not in seen_runs:candidates.append(run);seen_runs.add(run['id'])
    if wanted and not candidates:
        candidates=archives.client.api(f'repos/{archives.repo}/actions/workflows/official_competition.yaml/runs?branch=main&per_page=30')['workflow_runs']
    inspected = 0
    for run in candidates:
        if not wanted or inspected >= max_archives or time.monotonic()>=deadline:
            break
        if (run.get('head_branch') != 'main' or run.get('path') != '.github/workflows/official_competition.yaml' or
            run.get('head_repository',{}).get('full_name')!=archives.repo):
            raise ValueError('Untrusted competition recovery workflow')
        artifacts = archives.client.api(f'repos/{archives.repo}/actions/runs/{run["id"]}/artifacts?per_page=100')['artifacts']
        for artifact in artifacts:
            if inspected >= max_archives or not wanted:
                break
            name = artifact['name']
            if artifact.get('expired') or not (name.startswith('futureeval-official-evidence-') or name == 'futureeval-official-state'):
                continue
            inspected += 1
            try:
                with archives.open(artifact) as archive:
                    for ident, item in list(wanted.items()):
                        path=item['path']
                        if path not in archive.members:
                            continue
                        if item['size']+report['attempted_uncompressed_bytes'] > max_bytes:
                            break
                        report['attempted_uncompressed_bytes']+=item['size']
                        raw = archive.read(path, 64*1024**2)
                        if len(raw) != item['size'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                            continue
                        save_blob(root, raw, item['sha256'], item['size'])
                        report['recovered_bytes'] += item['size']
                        report['recovered'].append({'path': path, 'sha256': item['sha256'],
                                                   'artifact_id': str(artifact['id']), 'run_id': str(run['id'])})
                        del wanted[ident]
                    atomic_json(root/'recovery-progress.json',report)
            except Exception as exc:
                report['errors'].append({'artifact_id': str(artifact['id']), 'error_type': type(exc).__name__, 'reason': str(exc)[:180]})
    report.update(finished_at=clock(), inspected_archives=inspected,
                  remaining_competition_objects=list(wanted.values()),time_slice_exhausted=time.monotonic()>=deadline)
    atomic_json(root/'recovery-latest.json', report)
    return report


def run_locked(root,**kwargs):
    from ForecastAgent.runtime.task_lock import task_lock
    control=Path(root)/'recovery-control';control.mkdir(parents=True,exist_ok=True)
    with task_lock(control):return recover(root,**kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='E:/metaculus_data')
    parser.add_argument('--max-mib', type=int, default=256)
    parser.add_argument('--max-archives', type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.max_mib <= 1024 or not 1 <= args.max_archives <= 30:
        parser.error('Recovery bounds invalid')
    result = run_locked(args.root, max_bytes=args.max_mib*1024**2, max_archives=args.max_archives)
    print(json.dumps({k: v for k, v in result.items() if k not in ('recovered', 'remaining_competition_objects')} |
                     {'recovered_count': len(result['recovered']), 'remaining_count': len(result['remaining_competition_objects'])}))


if __name__ == '__main__':
    main()
