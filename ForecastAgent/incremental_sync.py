"""Import missing content only; whole recovery ZIPs remain on GitHub."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from ForecastAgent.local_transfer import atomic_json, TransferDeferred
from ForecastAgent.infrastructure.local_sync_export import PREFIX, SCHEMA, STREAMS


LEGACY_CHECKPOINTS = {'market-repricing-v2-worker-state', 'market-repricing-v2-monitor-state',
                      'futureeval-official-state', 'futureeval-monitor-state'}
LIVE_WORKFLOWS = {'market_research_worker.yaml', 'market_research_monitor.yaml',
                  'official_competition.yaml', 'run_bot_on_tournament.yaml'}
RECOVERY_HEADS = LEGACY_CHECKPOINTS | {'futureeval-monitor-health', 'futureeval-official-control'}


def automatic(name):
    return (name.startswith(PREFIX) or name in LEGACY_CHECKPOINTS or
            name in {'futureeval-monitor-health', 'futureeval-official-control'} or
            name.startswith('futureeval-official-evidence-') or name.startswith('futureeval-questions-'))


def validate_expected(expected):
    result = {}
    for item in expected:
        name, size, digest = item['path'], item['size_bytes'], item.get('sha256')
        path = PurePosixPath(name)
        if (not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts or
                str(path) != name or name in result or type(size) is not int or size < 0 or
                digest is not None and not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('Invalid local catalog member')
        result[name] = {'size_bytes': size, 'sha256': digest}
    if len(result) > 50000 or sum(item['size_bytes'] for item in result.values()) > 2 * 1024 ** 3:
        raise ValueError('Local catalog exceeds import bounds')
    return result


def all_local(root, expected):
    """Cheap preflight only; every reused byte is still hash-verified on import."""
    for value in expected.values():
        digest = value['sha256']
        if not digest:
            return False
        path = Path(root) / 'blobs' / 'sha256' / digest[:2] / digest
        if not path.is_file() or path.stat().st_size != value['size_bytes']:
            return False
    return True


def fallback_manifest(archive, name):
    """Migrate sealed existing artifacts without first downloading their bodies."""
    documents = {}
    candidates = ('inventory.json', 'checkpoint-inventory.json', 'delta-manifest.json', 'archive_manifest.json')
    manifest_name = next((path for path in candidates if path in archive.members), None)
    if manifest_name:
        data = archive.read(manifest_name, maximum=16 * 1024 * 1024)
        documents[manifest_name] = data
        payload = json.loads(data)
        entries = payload if manifest_name == 'archive_manifest.json' else payload['files']
        expected = []
        for path, value in entries.items():
            digest = value if isinstance(value, str) else value['sha256']
            if path in archive.members:
                size = archive.members[path].file_size
            elif path.endswith('/.lock.recovery') and digest == hashlib.sha256(b'0').hexdigest():
                size = 1
                documents[path] = b'0'
            else:
                raise ValueError('Sealed artifact member is missing: ' + path)
            if isinstance(value, dict) and value['size_bytes'] != size:
                raise ValueError('Publisher member size differs from ZIP directory')
            expected.append({'path': path, 'sha256': digest, 'size_bytes': size})
        expected.append({'path': manifest_name, 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)})
        declared = {item['path'] for item in expected}
        if any(path not in declared and not path.endswith('.tmp') for path in archive.members):
            raise ValueError('Sealed artifact has undeclared members')
        return validate_expected(expected), documents, 'publisher_sha256_and_member_crc'
    # Old monitor artifacts have no publisher hashes; preserve that distinction.
    if name in {'futureeval-monitor-state', 'futureeval-monitor-health', 'futureeval-official-control'} or name.startswith('futureeval-questions-'):
        return validate_expected([{'path': path, 'sha256': None, 'size_bytes': info.file_size}
                                  for path, info in archive.members.items()]), {}, 'member_crc_and_local_sha256_only'
    raise ValueError('Automatic artifact has no verifiable file catalog')


def import_members(root, db, client, repo, metadata, budget, expected=None):
    from ForecastAgent.local_sync import add_records, blob, utc
    ident = str(metadata['id'])
    run_id = str(metadata['workflow_run']['id'])
    name = metadata['name']
    report = {'artifact_id': ident, 'run_id': run_id, 'name': name,
              'source_zip_bytes': metadata['size_in_bytes'], 'original_zip_saved': False,
              'fetched_files': 0, 'reused_files': 0, 'avoided_compressed_member_bytes': 0}
    declared = validate_expected(expected) if expected is not None else None
    cached_only = declared is not None and all_local(root, declared)
    if cached_only:
        from contextlib import nullcontext
        from types import SimpleNamespace
        context = nullcontext(SimpleNamespace(members={path: SimpleNamespace(file_size=value['size_bytes'], compress_size=0)
                                                       for path, value in declared.items()}))
    else:
        context = client.open_zip(root, repo, metadata, budget)
    report['source_zip_opened'] = not cached_only
    with context as archive:
        if expected is None:
            expected, documents, assurance = fallback_manifest(archive, name)
        else:
            expected, documents = declared, {}
            assurance = 'publisher_sha256_reused_local_bytes' if cached_only else 'publisher_sha256_and_member_crc'
            if set(expected) != set(archive.members):
                raise ValueError('Catalog does not cover exact source artifact members')
        report.update(expected_files=len(expected), assurance=assurance)
        for path, info in sorted(expected.items()):
            if path not in archive.members and path not in documents:
                raise ValueError('Source member is unavailable')
            if path in archive.members and archive.members[path].file_size != info['size_bytes']:
                raise ValueError('Catalog and member sizes differ')
            digest = info['sha256']
            local = Path(root) / 'blobs' / 'sha256' / digest[:2] / digest if digest else None
            existing = db.execute('SELECT sha256,size_bytes FROM files WHERE repo=? AND artifact_id=? AND path=?',
                                  (repo, ident, path)).fetchone()
            if digest and local.is_file():
                data = local.read_bytes()
                if len(data) != info['size_bytes'] or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('Existing local object failed integrity validation')
                report['reused_files'] += 1
                if path in archive.members:
                    report['avoided_compressed_member_bytes'] += archive.members[path].compress_size
            else:
                data = documents[path] if path in documents else archive.read(path)
                if len(data) != info['size_bytes'] or digest and hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('Publisher file hash mismatch: ' + path)
                digest = blob(root, data)
                report['fetched_files'] += 1
            if existing and (existing[0] != digest or existing[1] != len(data)):
                raise ValueError('Previously indexed source artifact member changed')
            if not existing:
                db.execute('INSERT INTO files VALUES(?,?,?,?,?)', (repo, ident, path, digest, len(data)))
                add_records(db, repo, ident, path, data, digest)
                # Completed objects remain committed across a later traffic pause.
                db.commit()
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(repo,artifact_id) DO UPDATE SET '
                   'status=CASE WHEN artifacts.status="imported" THEN "imported" ELSE "files_imported" END,'
                   'imported_at=excluded.imported_at,error=NULL',
                   (repo, ident, run_id, name, 'files_imported', None, utc(), None))
        db.execute('DELETE FROM retries WHERE repo=? AND artifact_id=?', (repo, ident))
        db.execute('INSERT OR REPLACE INTO transfer_imports VALUES(?,?,?,?,?)',
                   (repo, ident, utc(), 'complete_files', json.dumps(report)))
        db.commit()
    return report


def import_catalog(root, db, client, repo, metadata, budget):
    """Read lightweight heads first, then independently import all missing bytes."""
    from ForecastAgent.local_sync import add_records, blob, utc
    ident, run_id = str(metadata['id']), str(metadata['workflow_run']['id'])
    embedded = {}
    with client.open_zip(root, repo, metadata, budget) as archive:
        data = archive.read('sync-manifest.json', maximum=16 * 1024 * 1024)
        catalog = json.loads(data)
        declarations = catalog.get('embedded', {})
        if (not isinstance(declarations, dict) or any(not re.fullmatch('[0-9a-f]{64}', digest) or
                name != 'embedded/' + digest + '.json' for digest, name in declarations.items()) or
                set(archive.members) != {'sync-manifest.json', 'summary.json'} | set(declarations.values())):
            raise ValueError('Unexpected lightweight catalog members')
        if sum(archive.members[name].file_size for name in declarations.values()) > 8 * 1024 ** 2:
            raise ValueError('Embedded control originals exceed bounds')
        for digest, path in declarations.items():
            content = archive.read(path, maximum=4 * 1024 ** 2)
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('Embedded control original failed its catalog hash')
            embedded[path] = content
        summary_data = archive.read('summary.json', maximum=32 * 1024 * 1024)
    if (catalog.get('schema') != SCHEMA or catalog.get('repo') != repo or catalog.get('run_id') != run_id or
            catalog.get('stream') not in STREAMS or metadata['name'] != PREFIX + catalog['stream']):
        raise ValueError('Local catalog provenance does not match its Actions artifact')
    summary = json.loads(summary_data)
    if summary.get('run_id') != run_id or summary.get('stream') != catalog['stream']:
        raise ValueError('Summary and catalog identities differ')
    for path, content in [('sync-manifest.json', data), ('summary.json', summary_data), *embedded.items()]:
        digest = blob(root, content)
        db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)', (repo, ident, path, digest, len(content)))
        add_records(db, repo, ident, path, content, digest)
    db.commit()
    # A newest summary is useful even if the following body transfer is paused.
    target = Path(root) / 'sync-catalogs' / catalog['stream'] / 'latest-summary.json'
    previous = json.loads(target.read_text()) if target.exists() else {}
    if int(previous.get('run_id', '0')) <= int(run_id):
        atomic_json(target, summary)
    artifacts = client.api(f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100')['artifacts']
    names = [source['artifact_name'] for source in catalog.get('sources', [])]
    if not names or len(set(names)) != len(names):
        raise ValueError('Catalog contains duplicate or empty source declarations')
    reports = []
    # Official changed-evidence artifacts can also satisfy a rotated checkpoint.
    for source in sorted(catalog['sources'], key=lambda item: item['artifact_name'] in LEGACY_CHECKPOINTS):
        if source.get('run_id') != run_id:
            raise ValueError('Catalog may not redirect to a different Actions run')
        matches = [item for item in artifacts if item['name'] == source['artifact_name']]
        declared = validate_expected(source['files'])
        declared_id = source.get('artifact_id')
        if declared_id is not None and not str(declared_id).isdecimal():
            raise ValueError('Catalog source artifact ID is invalid')
        if len(matches) != 1:
            if not matches and declared_id and all_local(root, declared):
                info = {'id': int(declared_id), 'name': source['artifact_name'], 'size_in_bytes': None,
                        'workflow_run': {'id': int(run_id)}, 'expired': True}
                reports.append(import_members(root, db, client, repo, info, budget, source['files']))
                continue
            raise RuntimeError('Catalog source is missing or expired with unknown local bytes: ' + source['artifact_name'])
        if declared_id and str(matches[0]['id']) != str(declared_id):
            raise ValueError('Catalog and source artifact IDs differ')
        if all_local(root, declared):
            # Exact cached originals can be linked even after the ZIP expires.
            info = dict(matches[0])
            info.setdefault('workflow_run', {'id': int(run_id)})
        else:
            if matches[0].get('expired'):
                raise RuntimeError('Catalog source retention expired with missing local originals')
            info = client.api(f'repos/{repo}/actions/artifacts/{matches[0]["id"]}')
        if str(info['workflow_run']['id']) != run_id:
            raise ValueError('Catalog source run identity changed')
        previous_status = db.execute('SELECT status FROM artifacts WHERE repo=? AND artifact_id=?',
                                    (repo, str(info['id']))).fetchone()
        if previous_status and previous_status[0] in {'imported', 'files_imported'}:
            indexed = {path: {'sha256': digest, 'size_bytes': size} for path, digest, size in db.execute(
                'SELECT path,sha256,size_bytes FROM files WHERE repo=? AND artifact_id=?', (repo, str(info['id'])))}
            if indexed != declared:
                raise ValueError('Catalog differs from already imported source members')
            reports.append({'artifact_id': str(info['id']), 'cached': True})
        else:
            reports.append(import_members(root, db, client, repo, info, budget, source['files']))
    db.execute('INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
               (repo, ident, run_id, metadata['name'], 'files_imported', None, utc(), None))
    db.execute('INSERT OR REPLACE INTO transfer_imports VALUES(?,?,?,?,?)',
               (repo, ident, utc(), 'complete_files', json.dumps({'catalog_sources': reports,
                'original_zip_saved': False, 'all_catalog_members_imported': True})))
    db.commit()
    return {'artifact_id': ident, 'catalog_sources': reports}


def trusted_run(db, repo, run_id):
    row = db.execute('SELECT body_json FROM run_observations WHERE repo=? AND run_id=? ORDER BY updated_at DESC LIMIT 1',
                     (repo, run_id)).fetchone()
    if row is None:
        return False
    run = json.loads(row[0])
    return (run.get('head_branch') == 'main' and run.get('head_repository', {}).get('full_name') == repo and
            PurePosixPath(run.get('path', '')).name in LIVE_WORKFLOWS)


def execute(root, db, client, repo, budget, maximum=20, *, discovery_failures=0):
    from ForecastAgent.local_sync import utc, GitHubReadError
    imported, failures, deferred, reports, errors = 0, discovery_failures, 0, [], []
    # Retire superseded heads before retry-deadline filtering. Otherwise old
    # deleted health/control artifacts keep consuming metadata requests forever.
    heads = db.execute('SELECT artifact_id,run_id,name,status FROM artifacts WHERE repo=?', (repo,)).fetchall()
    trust = {}
    def trusted(run_id):
        if run_id not in trust:
            trust[run_id] = trusted_run(db, repo, run_id)
        return trust[run_id]
    latest = {}
    for _, run_id, name, status in heads:
        if name in RECOVERY_HEADS and status != 'expired' and trusted(run_id):
            latest[name] = max(latest.get(name, 0), int(run_id))
    for ident, run_id, name, status in heads:
        if name in RECOVERY_HEADS and status in {'pending', 'failed', 'transfer_wait'} and int(run_id) < latest.get(name, 0):
            db.execute('UPDATE artifacts SET status="checkpoint_remote_only" WHERE repo=? AND artifact_id=?', (repo, ident))
            deferred += 1
    db.commit()
    rows = db.execute('SELECT a.artifact_id,a.run_id,a.name FROM artifacts a LEFT JOIN retries r '
        'ON a.repo=r.repo AND a.artifact_id=r.artifact_id WHERE a.repo=? AND a.status IN ("pending","failed","transfer_wait") '
        'AND (r.next_retry_at IS NULL OR r.next_retry_at<=?) '
        'ORDER BY CASE WHEN a.name LIKE ? THEN 0 ELSE 1 END,CAST(a.run_id AS INTEGER) DESC,a.artifact_id',
        (repo, utc(), PREFIX + '%')).fetchall()
    seen, attempted = set(), 0
    for ident, run_id, name in rows:
        current = db.execute('SELECT status FROM artifacts WHERE repo=? AND artifact_id=?', (repo, ident)).fetchone()
        if current[0] in {'imported', 'files_imported'}:
            continue
        if not automatic(name) or not trusted(run_id):
            db.execute('UPDATE artifacts SET status="remote_only" WHERE repo=? AND artifact_id=?', (repo, ident))
            deferred += 1
            continue
        # Only the newest unimported legacy recovery checkpoint is needed. Every
        # published catalog remains independently eligible, including catch-up.
        if name in LEGACY_CHECKPOINTS:
            if int(run_id) < latest.get(name, 0) or name in seen:
                db.execute('UPDATE artifacts SET status="checkpoint_remote_only" WHERE repo=? AND artifact_id=?', (repo, ident))
                deferred += 1
                continue
            seen.add(name)
        if attempted >= maximum:
            continue
        attempted += 1
        try:
            info = client.api(f'repos/{repo}/actions/artifacts/{ident}')
            if info.get('expired'):
                db.execute('UPDATE artifacts SET status="expired",error="Remote retention expired" WHERE repo=? AND artifact_id=?', (repo, ident))
                deferred += 1
                continue
            if info['name'] != name or str(info['workflow_run']['id']) != run_id:
                raise ValueError('Artifact identity does not match discovered run')
            result = import_catalog(root, db, client, repo, info, budget) if name.startswith(PREFIX) else import_members(root, db, client, repo, info, budget)
            reports.append(result)
            imported += 1
            db.execute('DELETE FROM retries WHERE repo=? AND artifact_id=?', (repo, ident))
        except TransferDeferred:
            db.execute('UPDATE artifacts SET status="transfer_wait",error="Local transfer limit; ranges retained" WHERE repo=? AND artifact_id=?', (repo, ident))
            deferred += 1
            break
        except Exception as exc:
            if isinstance(exc, GitHubReadError) and exc.status == 404:
                try:
                    listed = client.api(f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100')['artifacts']
                    gone = not any(str(item['id']) == ident for item in listed)
                except Exception:
                    gone = False
                if gone:
                    db.execute('UPDATE artifacts SET status="expired",error="Source artifact no longer listed" '
                               'WHERE repo=? AND artifact_id=?', (repo, ident))
                    db.execute('DELETE FROM retries WHERE repo=? AND artifact_id=?', (repo, ident))
                    deferred += 1
                    continue
            failures += 1
            errors.append({'artifact_id': ident, 'name': name, 'error_type': type(exc).__name__,
                           'error_detail': str(exc)[:240]})
            db.execute('UPDATE artifacts SET status="failed",error=? WHERE repo=? AND artifact_id=?', (type(exc).__name__, repo, ident))
            previous = db.execute('SELECT attempts FROM retries WHERE repo=? AND artifact_id=?', (repo, ident)).fetchone()
            count = previous[0] + 1 if previous else 1
            from datetime import timedelta
            retry = (datetime.now(timezone.utc) + timedelta(minutes=min(1440, 15 * 2 ** min(count - 1, 7)))).isoformat()
            db.execute('INSERT OR REPLACE INTO retries VALUES(?,?,?,?)', (repo, ident, count, retry))
        finally:
            db.commit()
    result = {'status': 'partial' if failures else 'transfer_wait' if any(
        row[0] == 'transfer_wait' for row in db.execute('SELECT status FROM artifacts WHERE repo=?', (repo,))) else 'success',
        'mode': 'incremental', 'imported': imported, 'failed': failures, 'deferred': deferred,
        'traffic': budget.snapshot(), 'imports': reports, 'errors': errors,
        'discovery_incomplete': discovery_failures > 0}
    db.execute('INSERT INTO sync_events VALUES(?,?,?)', (utc(), result['status'], json.dumps(result)))
    db.commit()
    atomic_json(Path(root) / 'transfer-status.json', result)
    return result
