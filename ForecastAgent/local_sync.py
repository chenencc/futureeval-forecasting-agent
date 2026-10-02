"""Pull Actions captures into an immutable local store and structured SQLite index."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile

from ForecastAgent.runtime.task_lock import task_lock

DEFAULT_ROOT = Path('E:/metaculus_data')
DEFAULT_REPO = 'chenencc/futureeval-forecasting-agent'
MAX_ZIP_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


class ManagedConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def utc():
    return datetime.now(timezone.utc).isoformat()


def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'index.sqlite3', timeout=30, factory=ManagedConnection)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO metadata VALUES('schema', 'forecast_local_store_v1');
        CREATE TABLE IF NOT EXISTS runs(repo TEXT, run_id TEXT, workflow TEXT, commit_sha TEXT,
            conclusion TEXT, completed_at TEXT, discovery_status TEXT, PRIMARY KEY(repo,run_id));
        CREATE TABLE IF NOT EXISTS run_observations(repo TEXT,run_id TEXT,updated_at TEXT,body_json TEXT,
            PRIMARY KEY(repo,run_id,updated_at));
        CREATE TABLE IF NOT EXISTS artifacts(repo TEXT, artifact_id TEXT, run_id TEXT, name TEXT,
            status TEXT, archive_sha256 TEXT, imported_at TEXT, error TEXT,
            PRIMARY KEY(repo,artifact_id));
        CREATE TABLE IF NOT EXISTS files(repo TEXT, artifact_id TEXT, path TEXT, sha256 TEXT,
            size_bytes INTEGER, PRIMARY KEY(repo,artifact_id,path));
        CREATE TABLE IF NOT EXISTS records(repo TEXT, artifact_id TEXT, path TEXT, kind TEXT,
            record_id TEXT, question_id TEXT, source_sha256 TEXT, body_json TEXT,
            PRIMARY KEY(repo,artifact_id,path,kind,record_id));
        CREATE INDEX IF NOT EXISTS records_question ON records(question_id,kind);
        CREATE INDEX IF NOT EXISTS files_hash ON files(sha256);
        CREATE VIEW IF NOT EXISTS question_versions AS SELECT repo,artifact_id,path,question_id,
            json_extract(body_json,'question') AS question,
            json_extract(body_json,'as_of_utc') AS as_of_utc,body_json
            FROM records WHERE kind='question';
        CREATE VIEW IF NOT EXISTS document_versions AS SELECT repo,artifact_id,path,question_id,record_id,
            json_extract(body_json,'$.url') AS url,
            json_extract(body_json,'$.retrieved_at_utc') AS captured_at,
            json_extract(body_json,'$.published_at') AS published_at,
            json_extract(body_json,'$.temporal_status') AS temporal_status,
            json_extract(body_json,'$.sha256') AS original_sha256,body_json
            FROM records WHERE kind='page';
        CREATE VIEW IF NOT EXISTS search_attempts AS SELECT repo,artifact_id,path,question_id,record_id,
            json_extract(body_json,'$.query') AS query,
            json_extract(body_json,'$.status') AS status,body_json
            FROM records WHERE kind='search';
        CREATE VIEW IF NOT EXISTS model_call_versions AS SELECT repo,artifact_id,path,question_id,
            json_extract(body_json,'$.request.model') AS model,
            json_extract(body_json,'$.status') AS status,
            json_extract(body_json,'$.response.usage.total_tokens') AS total_tokens,
            json_extract(body_json,'$.duration_seconds') AS duration_seconds,body_json
            FROM records WHERE kind='model_transport';
        CREATE TABLE IF NOT EXISTS sync_events(at TEXT, status TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS retries(repo TEXT,artifact_id TEXT,attempts INTEGER,next_retry_at TEXT,
            PRIMARY KEY(repo,artifact_id));
    ''')
    db.commit()
    return db


def blob(root, data):
    digest = hashlib.sha256(data).hexdigest()
    path = Path(root) / 'blobs' / 'sha256' / digest[:2] / digest
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Existing local blob failed integrity validation')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(data)
        temporary.replace(path)
    return digest


def add_records(db, repo, artifact, path, data, digest):
    if not path.endswith('.json'):
        return
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError):
        return
    def add(kind, key, body, ident=None):
        db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?,?,?)',
                   (repo, artifact, path, kind, str(key), str(ident) if ident is not None else None,
                    digest, json.dumps(body, ensure_ascii=False)))
    add('json_document', 'root', payload)
    if not isinstance(payload, dict):
        # Blind question arrays remain indexed without reading evaluation labels.
        if isinstance(payload, list) and 'blind_inputs' in path:
            for item in payload:
                if isinstance(item, dict):
                    add('question', item.get('id'), item, item.get('id'))
        return
    if payload.get('schema') == 'competition-shadow-v1' and isinstance(payload.get('tasks'), dict):
        add('competition_queue', 'root', payload)
        for question_id, task in payload['tasks'].items():
            add('competition_question', question_id, task, question_id)
    if payload.get('schema') == 'competition-shadow-candidate-v1':
        add('competition_candidate', 'root', payload, payload.get('id'))
    if payload.get('schema') == 'live-evidence-bridge-v1':
        add('competition_evidence_bridge', 'root', payload, payload.get('question_id'))
    request = payload.get('request')
    ident = next((part for part in reversed(PurePosixPath(path).parts[:-1]) if part.isdecimal()), None)
    if isinstance(request, dict):
        ident = request.get('id', request.get('question_id', request.get('post_id', ident)))
    if isinstance(request, dict):
        add('question', 'request', request, ident)
        for kind, field in [('page', 'pages'), ('excerpt', 'excerpts'), ('search', 'searches'), ('exa_search', 'exa_searches'),
                            ('model_attempt', 'model_attempts'), ('tool_step', 'step_attempts'),
                            ('market_snapshot', 'market_snapshots'), ('quarantine', 'quarantine')]:
            values = payload.get(field, {})
            entries = values.items() if isinstance(values, dict) else enumerate(values)
            for key, value in entries:
                add(kind, key, value, ident)
        for kind, field in [('collection_result', 'result'), ('collection_result', 'collection_result'),
                            ('acceptance', 'acceptance'), ('temporal', 'temporal_provenance')]:
            if payload.get(field) is not None:
                add(kind, field, payload[field], ident)
    if '/model_calls/' in '/' + path:
        add('model_transport', 'response', payload, ident)
    # Index analysis separately from collection and provider search budgets.
    analysis_kinds = {'prediction.json': 'analysis_prediction', 'analysis.json': 'analysis_report',
                      'evidence-packet.json': 'analysis_evidence_packet', 'decision-response.json': 'analysis_decision',
                      'citation-audit.json': 'analysis_citation_audit'}
    if PurePosixPath(path).name in analysis_kinds:
        add(analysis_kinds[PurePosixPath(path).name], 'root', payload, ident)
    # Supplement records remain distinct from original collection quotas/results.
    if payload.get('schema') == 'evidence_supplement_v1':
        ident = payload.get('task_id', ident)
        add('supplement_manifest', 'root', payload, ident)
        for index, attempt in enumerate(payload.get('attempts', [])):
            add('supplement_attempt', index, attempt, ident)
        for index, gap in enumerate(payload.get('remaining_gaps', [])):
            add('supplement_gap', index, gap, ident)
        add('supplement_handoff', 'root', payload.get('analysis_handoff', {}), ident)
    if isinstance(payload.get('supplement_provenance'), dict):
        add('supplement_capture', 'root', payload, payload['supplement_provenance'].get('task_id', ident))
    if any('/' + directory + '/' in '/' + path for directory in ('ultra-http', 'mercury-http', 'health-http')):
        add('analysis_provider_transport', 'response', payload, ident)
    if path.endswith('state.json'):
        add('monitor_state', 'state', payload)


def import_zip(root, archive, *, repo=DEFAULT_REPO, run_id='local', artifact_id=None, name='local-import', nested=False):
    archive = Path(archive)
    if archive.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError('Archive exceeds the local import size limit')
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    artifact_id = str(artifact_id or 'local-' + archive_hash)
    root = Path(root)
    with connect(root) as db:
        previous = db.execute('SELECT status,archive_sha256 FROM artifacts WHERE repo=? AND artifact_id=?',
                              (repo, artifact_id)).fetchone()
        if previous and previous[0] == 'imported':
            if previous[1] != archive_hash:
                raise ValueError('Previously imported artifact bytes changed')
            if not nested:
                import_nested(root,archive,repo,run_id,artifact_id,name)
            return {'status': 'cached', 'artifact_id': artifact_id}
        with zipfile.ZipFile(archive) as saved:
            members = saved.infolist()
            if len(members) > 50000 or sum(p.file_size for p in members) > MAX_EXPANDED_BYTES:
                raise ValueError('Expanded archive exceeds the local import limit')
            paths = set()
            for item in members:
                path = PurePosixPath(item.filename.replace('\\', '/'))
                if path.is_absolute() or '..' in path.parts or ':' in item.filename or str(path) in paths:
                    raise ValueError('Unsafe or duplicate archive path')
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Archive symlinks are unsupported')
                paths.add(str(path))
            manifest = json.loads(saved.read('archive_manifest.json')) if 'archive_manifest.json' in saved.namelist() else None
            if manifest is not None and set(manifest) != {p.filename for p in members if not p.is_dir() and p.filename != 'archive_manifest.json'}:
                raise ValueError('Archive manifest does not cover all files')
            for item in members:
                if item.is_dir():
                    continue
                data = saved.read(item)
                digest = hashlib.sha256(data).hexdigest()
                if manifest is not None and item.filename != 'archive_manifest.json' and manifest[item.filename] != digest:
                    raise ValueError('Archive manifest hash mismatch')
                blob(root, data)
                path = str(PurePosixPath(item.filename.replace('\\', '/')))
                db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)',
                           (repo, artifact_id, path, digest, len(data)))
                add_records(db, repo, artifact_id, path, data, digest)
        target = root / 'archives' / archive_hash[:2] / (archive_hash + '.zip')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix('.tmp')
            shutil.copyfile(archive, temporary)
            temporary.replace(target)
        db.execute('INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
                   (repo, artifact_id, str(run_id), name, 'imported', archive_hash, utc(), None))
        db.commit()
    # GitHub wraps the portable historical ZIP in another artifact ZIP.
    # Preserve both byte streams and index the inner campaign exactly once.
    if not nested:
        import_nested(root,archive,repo,run_id,artifact_id,name)
    return {'status': 'imported', 'artifact_id': artifact_id, 'files': len(members)}


def import_nested(root,archive,repo,run_id,artifact_id,name):
    with zipfile.ZipFile(archive) as saved:
        names = [p.filename for p in saved.infolist() if PurePosixPath(p.filename).name == 'historical-2026-135.zip']
        if len(names) > 1:
            raise ValueError('Ambiguous nested campaign archive')
        for member in names:
            with tempfile.TemporaryDirectory(dir=root) as staging:
                target = Path(staging) / 'campaign.zip'
                target.write_bytes(saved.read(member))
                import_zip(root,target,repo=repo,run_id=run_id,
                           artifact_id=artifact_id + ':campaign', name=name + ':campaign',nested=True)


class GitHub:
    """Use standard gh authentication without extracting stored credentials."""
    def __init__(self, executable=None):
        self.executable = executable or os.environ.get('FORECAST_GH_PATH') or shutil.which('gh')
        if not self.executable:
            raise RuntimeError('GitHub CLI is required; install gh and complete gh auth login')
        result = subprocess.run([self.executable, 'auth', 'status', '--hostname', 'github.com'],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('GitHub CLI needs login; complete gh auth login on this computer')

    def api(self, endpoint):
        result = subprocess.run([self.executable, 'api', endpoint], capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError('GitHub read request failed; check login, permissions and rate limits')
        return json.loads(result.stdout)

    def download(self, repo, ident, target):
        with open(target, 'wb') as output:
            result = subprocess.run([self.executable, 'api', f'repos/{repo}/actions/artifacts/{ident}/zip'],
                                    stdout=output, stderr=subprocess.PIPE, timeout=300)
        if result.returncode:
            raise RuntimeError('Artifact download failed; check expiry and Actions read access')


def sync(root=DEFAULT_ROOT, repo=DEFAULT_REPO, client=None, max_downloads=20):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        db = connect(root)
        imported = failures = 0
        try:
            client = client or GitHub()
            marker_key = 'last_discovery:' + repo
            marker = db.execute('SELECT value FROM metadata WHERE key=?', (marker_key,)).fetchone()
            since = (datetime.fromisoformat(marker[0]) - timedelta(days=1)) if marker else datetime.now(timezone.utc) - timedelta(days=90)
            started = utc()
            for page in range(1, 101):
                runs = client.api(f'repos/{repo}/actions/runs?status=completed&per_page=100&page={page}')['workflow_runs']
                for run in runs:
                    if datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00')) < since:
                        continue
                    db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?) ON CONFLICT(repo,run_id) DO UPDATE SET '
                        'discovery_status=CASE WHEN runs.completed_at!=excluded.completed_at THEN "pending" ELSE runs.discovery_status END,'
                        'completed_at=excluded.completed_at,conclusion=excluded.conclusion,commit_sha=excluded.commit_sha',
                        (repo, str(run['id']), run.get('path', run.get('name')), run.get('head_sha'),
                         run.get('conclusion'), run['updated_at'], 'pending'))
                    db.execute('INSERT OR IGNORE INTO run_observations VALUES(?,?,?,?)',
                        (repo,str(run['id']),run['updated_at'],json.dumps(run,ensure_ascii=False)))
                db.commit()
                if not runs or all(datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) < since for r in runs):
                    db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (marker_key, started))
                    db.commit()
                    break
            else:
                raise RuntimeError('Discovery page limit reached; progress retained, cursor not advanced')
            pending = db.execute('SELECT run_id FROM runs WHERE repo=? AND discovery_status="pending" ORDER BY completed_at', (repo,)).fetchall()
            for (run_id,) in pending:
                try:
                    discovered = 0
                    for page in range(1, 101):
                        artifacts = client.api(f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100&page={page}')['artifacts']
                        discovered += len(artifacts)
                        for item in artifacts:
                            db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
                                (repo, str(item['id']), run_id, item['name'], 'expired' if item['expired'] else 'pending', None, None, None))
                        if len(artifacts) < 100:
                            break
                    else:
                        raise RuntimeError('Artifact discovery page limit reached')
                    completed = db.execute('SELECT completed_at FROM runs WHERE repo=? AND run_id=?',(repo,run_id)).fetchone()[0]
                    recent = datetime.fromisoformat(completed.replace('Z','+00:00')) > datetime.now(timezone.utc) - timedelta(hours=1)
                    if discovered or not recent:
                        db.execute('UPDATE runs SET discovery_status="listed" WHERE repo=? AND run_id=?', (repo, run_id))
                    db.commit()
                except Exception:
                    failures += 1
                    break
            todo = db.execute('SELECT a.artifact_id,a.run_id,a.name FROM artifacts a LEFT JOIN retries r '
                'ON a.repo=r.repo AND a.artifact_id=r.artifact_id WHERE a.repo=? AND a.status IN ("pending","failed") '
                'AND (r.next_retry_at IS NULL OR r.next_retry_at<=?) '
                'ORDER BY CASE a.status WHEN "pending" THEN 0 ELSE 1 END,CAST(a.run_id AS INTEGER) DESC,a.artifact_id LIMIT ?',
                (repo, utc(), max_downloads)).fetchall()
            for ident, run_id, name in todo:
                try:
                    with tempfile.TemporaryDirectory(dir=root) as staging:
                        target = Path(staging) / 'download.zip'
                        client.download(repo, ident, target)
                        import_zip(root, target, repo=repo, run_id=run_id, artifact_id=ident, name=name)
                    imported += 1
                    db.execute('DELETE FROM retries WHERE repo=? AND artifact_id=?',(repo,ident))
                    db.commit()
                except Exception as exc:
                    failures += 1
                    db.execute('UPDATE artifacts SET status="failed",error=? WHERE repo=? AND artifact_id=?',
                               (type(exc).__name__, repo, ident))
                    previous = db.execute('SELECT attempts FROM retries WHERE repo=? AND artifact_id=?',(repo,ident)).fetchone()
                    count = (previous[0] if previous else 0) + 1
                    next_at = (datetime.now(timezone.utc) + timedelta(minutes=min(1440,15 * 2 ** min(count-1,7)))).isoformat()
                    db.execute('INSERT OR REPLACE INTO retries VALUES(?,?,?,?)',(repo,ident,count,next_at))
                    db.commit()
            state = 'partial' if failures else 'success'
            db.execute('INSERT INTO sync_events VALUES(?,?,?)', (utc(), state, json.dumps({'imported': imported, 'failed': failures})))
            db.commit()
            return {'status': state, 'imported': imported, 'failed': failures}
        except Exception as exc:
            db.execute('INSERT INTO sync_events VALUES(?,?,?)', (utc(), 'blocked', str(exc)[:300]))
            db.commit()
            raise
        finally:
            db.close()


def status(root):
    with connect(root) as db:
        counts = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ['runs', 'artifacts', 'files', 'records']}
        counts['artifact_states'] = dict(db.execute('SELECT status,COUNT(*) FROM artifacts GROUP BY status'))
        counts['record_kinds'] = dict(db.execute('SELECT kind,COUNT(*) FROM records GROUP BY kind'))
        counts['last_sync'] = db.execute('SELECT at,status,detail FROM sync_events ORDER BY rowid DESC LIMIT 1').fetchone()
        return counts


def publish_status(root):
    report = {'schema':'forecast_local_status_v1','generated_at_utc':utc(),**status(root)}
    path=Path(root)/'status.json'
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init', 'sync', 'import', 'status'])
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--run-id', default='local')
    parser.add_argument('--gh-path')
    args = parser.parse_args()
    if args.gh_path:
        os.environ['FORECAST_GH_PATH'] = args.gh_path
    if args.action == 'sync':
        result = sync(args.root, args.repo)
    elif args.action == 'import':
        if not args.archive:
            parser.error('--archive is required')
        args.root.mkdir(parents=True, exist_ok=True)
        with task_lock(args.root):
            result = import_zip(args.root, args.archive, repo=args.repo, run_id=args.run_id)
    else:
        result = status(args.root)
    publish_status(args.root)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
