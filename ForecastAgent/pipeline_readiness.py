"""Per-snapshot replay receipts, separate from worker success and model accuracy.

Only saved local objects are inspected. No acquisition, models or submissions.
Derived receipts never replace native clocks, inputs, forecasts or archives.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import gzip
import json
from pathlib import Path


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def instant(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except ValueError:
        return None


def body(db, table, source, clock_field, problems, cache):
    ident = source.get('display_sha256')
    if ident not in cache:
        row = db.execute('SELECT text,byte_length FROM '+table+' WHERE display_sha256=?',
                         (ident,)).fetchone()
        cache[ident] = bool(row and len(row[0].encode()) == row[1] and
                            hashlib.sha256(row[0].encode()).hexdigest() == ident)
    if not cache[ident]:
        problems.add('local_body_missing_or_corrupt')
    if instant(source.get(clock_field)) is None:
        problems.add('capture_clock_missing')
    if not source.get('url') or not source.get('body_sha256'):
        problems.add('source_identity_missing')
    return {k: source.get(k) for k in ('url', 'body_sha256', 'display_sha256', clock_field)}


def receipt(key, batch, processing, problems, manifest):
    manifest = {'schema': 'forecast-replay-manifest-v1', 'key': key, 'batch_id': batch,
                'processing_status': processing, 'replay_ready': not problems,
                'gaps': sorted(problems), **manifest,
                'scope': 'Saved input replay, not evidence truth, resolution or forecast accuracy'}
    return {**manifest, 'manifest_sha256': sha(manifest)}


def market(db):
    from ForecastAgent.market_store.web_read import read_task
    rows, cache = [], {}
    for record in db.execute('SELECT key FROM markets ORDER BY key').fetchall():
        key = record[0]
        try:
            task = read_task(db, key, with_history=False)
            selected = next((b for b in task['revisions'] if b['id'] == task['selected_revision_id']), {})
            materials = selected.get('materials') or {}
            forecast = selected.get('forecast') or {}
            problems = set()
            if task['worker_status'] != 'completed':
                problems.add('worker_not_completed')
            if not task.get('rules'):
                problems.add('frozen_rules_missing')
            if selected.get('material_status') != 'worker_material_available':
                problems.add(selected.get('material_status') or 'material_snapshot_missing')
            routes = materials.get('route_statuses') or {}
            original = forecast.get('arms', {}).get('original') or {}
            if original.get('status') != 'completed':
                problems.add('original_score_missing')
            if routes.get('original', {}).get('status') != 'model_input_verified':
                problems.add('original_model_input_not_bound')
            frozen = original.get('sealed_at') or forecast.get('sealed_at')
            seal = instant(frozen)
            if seal is None:
                problems.add('forecast_clock_missing')
            sources = materials.get('sources') or []
            if not sources:
                problems.add('no_readable_originals')
            dependencies = [body(db, 'web_documents', s, 'captured_at', problems, cache) for s in sources]
            if seal and any(instant(s.get('captured_at')) and instant(s['captured_at']) > seal for s in sources):
                problems.add('capture_after_forecast')
            bound = materials.get('sources_by_arm', {}).get('original') or []
            available = {(s.get('url'), s.get('body_sha256')) for s in sources}
            if not bound or any((s.get('url'), s.get('body_sha256')) not in available for s in bound):
                problems.add('original_source_binding_unavailable')
            if not materials.get('material_sha256'):
                problems.add('material_identity_missing')
            raw_dependencies=[]
            if selected.get('source_kind') == 'original_worker':
                root=Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
                # Native market paths use the store's canonical digest.
                from ForecastAgent.market_store.store import digest
                prefix='tasks/'+digest(key)+'/'
                stage=task.get('analysis_stage') or 'analysis'
                base=(materials.get('provenance') or {}).get('material_path')
                names=[base,stage+'/original/state.json',stage+'/original/result.json']
                for name in names:
                    matches=db.execute('SELECT sha256,size_bytes FROM material_refs WHERE market_key=? AND path=? '
                                       'ORDER BY rowid DESC',(key,prefix+str(name))).fetchall()
                    found=None
                    for ref in matches:
                        for path in (root/'objects'/ref[0][:2]/ref[0],root.parent.parent/'blobs/sha256'/ref[0][:2]/ref[0]):
                            if not path.is_file() or path.stat().st_size!=ref[1]:continue
                            raw=path.read_bytes()
                            if hashlib.sha256(raw).hexdigest()!=ref[0]:continue
                            value=json.loads(raw)
                            if name==base and digest(value)!=materials.get('material_sha256'):continue
                            if name.endswith('/state.json') and digest(value)!=routes.get('original',{}).get('state_sha256'):continue
                            if name.endswith('/result.json') and any(value.get(k)!=original[k] for k in
                                ('eventual_probability_yes','response_sha256','state_sha256') if k in original):continue
                            found={'path':name,'sha256':ref[0],'size_bytes':ref[1]};break
                        if found:break
                    if found:raw_dependencies.append(found)
                    else:problems.add('native_replay_object_missing')
            elif selected.get('source_kind') == 'material_revision':
                # A sanitized projection alone is not the complete original
                # experiment snapshot. Its native revision remains mandatory.
                rev=db.execute('SELECT data FROM revisions WHERE market_key=? AND revision_id=?',
                               (key,selected.get('id'))).fetchone()
                value=json.loads(rev[0]) if rev else {}
                folder=Path(value.get('result_file','')).parent
                for name in ('frozen-input.json','mapped-state.json','revision-result.json'):
                    path=folder/name
                    if not path.is_file():problems.add('native_revision_object_missing')
                    else:raw_dependencies.append({'path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            rows.append(receipt(key, selected.get('id'), task['worker_status'], problems, {
                'rules_sha256': sha(task.get('rules')), 'created_at': task.get('created_at'),
                'enrolled_at': task.get('enrolled_at'), 'forecast_at': frozen,
                'forecast_fingerprint': selected.get('forecast_fingerprint'),
                'material_sha256': materials.get('material_sha256'),
                'original_input': routes.get('original'), 'original_output': original,
                'source_kind': selected.get('source_kind'), 'native_dependencies':raw_dependencies,'sources': dependencies}))
        except (ValueError, KeyError, TypeError) as exc:
            rows.append(receipt(key, None, 'unknown', {'replay_validation_failed'},
                                {'error_type': type(exc).__name__, 'reason': str(exc)[:180]}))
    return summarize(rows)


def competition(db):
    rows, cache = [], {}
    for record in db.execute('SELECT key FROM questions ORDER BY key').fetchall():
        key = record[0]
        task = db.execute('SELECT stage FROM latest_tasks WHERE question_key=?', (key,)).fetchone()
        forecast = db.execute('SELECT * FROM latest_forecasts WHERE question_key=?', (key,)).fetchone()
        problems = set()
        if forecast is None:
            rows.append(receipt(key, None, task[0] if task else 'not_started',
                                {'forecast_not_recorded'}, {}))
            continue
        inputs = db.execute('SELECT * FROM model_inputs WHERE forecast_id=? ORDER BY observed_at DESC,rowid DESC',
                            (forecast['id'],)).fetchall()
        selected = next((r for r in inputs if r['status'] == 'model_input_verified'), None)
        if selected is None:
            problems.add('selected_model_input_not_bound')
        batch_id = selected['batch_id'] if selected else None
        batch = db.execute('SELECT * FROM bundles WHERE id=?', (batch_id,)).fetchone()
        bundle = json.loads(batch['data']) if batch else {}
        request = bundle.get('request') or {}
        if not request.get('resolution_criteria'):
            problems.add('frozen_rules_missing')
        sources = [dict(r) for r in db.execute('SELECT * FROM captures WHERE batch_id=?', (batch_id,))]
        if not sources:
            problems.add('no_readable_originals')
        dependencies = [body(db, 'documents', s, 'captured_at', problems, cache) for s in sources]
        state = (json.loads(selected['data']).get('state') or {}) if selected else {}
        explicit=(state.get('question') or {}).get('as_of_utc') or request.get('as_of_utc')
        clocks=[s['captured_at'] for s in sources if instant(s.get('captured_at'))]
        # Live releases intentionally have no retrospective cutoff. Derive an
        # honest last-capture clock, never opening time or import time, and keep
        # historical-cutoff eligibility separate from reproducibility.
        cutoff=instant(explicit)
        information_clock=explicit if cutoff else max(clocks,key=instant) if clocks else None
        if information_clock is None:problems.add('frozen_information_clock_missing')
        if cutoff and any(instant(s.get('captured_at')) and instant(s['captured_at']) > cutoff for s in sources):
            problems.add('capture_after_information_cutoff')
        if selected:
            from ForecastAgent.competition_store.store import native_hash
            if native_hash(state)!=selected['state_sha256']:problems.add('saved_model_input_hash_mismatch')
        if batch:
            path=batch['role'][6:] if batch['role'].startswith('local:') else 'tasks/'+key.split(':',1)[1]+'/'+batch['role']
            native=db.execute('SELECT o.* FROM file_refs f JOIN native_objects o ON o.sha256=f.sha256 '
                              'WHERE f.source_id=? AND f.path=?',(batch['source_id'],path)).fetchone()
            if native is None:problems.add('native_material_object_missing')
            else:
                raw=gzip.decompress(native['bytes']) if native['codec']=='gzip' else native['bytes']
                from ForecastAgent.market_store.store import digest
                if (hashlib.sha256(raw).hexdigest()!=native['sha256'] or len(raw)!=native['size_bytes'] or
                    digest(json.loads(raw))!=batch['bundle_sha256']):problems.add('native_material_hash_mismatch')
        available = {(s.get('url'), s.get('body_sha256')) for s in sources}
        bound = state.get('sources') or []
        if not bound or any((s.get('url'), s.get('body_sha256')) not in available for s in bound):
            problems.add('selected_source_binding_unavailable')
        rows.append(receipt(key, batch_id, task[0] if task else 'not_recorded', problems, {
            'rules_sha256': sha(request.get('resolution_criteria')),
            'information_clock':information_clock,'information_clock_role':'explicit_as_of' if cutoff else 'latest_recorded_capture',
            'historical_cutoff_verified':False,'forecast_id': forecast['id'],
            'candidate_sha256': forecast['candidate_sha256'], 'payload_sha256': sha(json.loads(forecast['payload'])),
            'input_sha256': selected['state_sha256'] if selected else None,
            'model': (json.loads(selected['data']).get('model') if selected else None),
            'material_sha256': batch['bundle_sha256'] if batch else None, 'sources': dependencies}))
    return summarize(rows)


def summarize(rows):
    return {'schema': 'forecast-replay-readiness-v1', 'total': len(rows),
            'ready': sum(r['replay_ready'] for r in rows),
            'not_ready': sum(not r['replay_ready'] for r in rows),
            'gap_counts': dict(Counter(g for r in rows for g in r['gaps'])), 'tasks': rows}


def persist(db, report):
    """Append content-addressed receipts and update only the derived latest head."""
    stamp = datetime.now(timezone.utc).isoformat()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS replay_manifests(manifest_sha256 TEXT PRIMARY KEY,
          question_key TEXT NOT NULL,batch_id TEXT,replay_ready INTEGER NOT NULL,
          manifest_json TEXT NOT NULL,first_checked_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS replay_heads(question_key TEXT PRIMARY KEY,
          manifest_sha256 TEXT NOT NULL REFERENCES replay_manifests(manifest_sha256),checked_at TEXT NOT NULL);
    ''')
    with db:
        for row in report['tasks']:
            db.execute('INSERT OR IGNORE INTO replay_manifests VALUES(?,?,?,?,?,?)',
                       (row['manifest_sha256'], row['key'], row['batch_id'], int(row['replay_ready']),
                        json.dumps(row, sort_keys=True, ensure_ascii=False), stamp))
            db.execute('INSERT INTO replay_heads VALUES(?,?,?) ON CONFLICT(question_key) DO UPDATE SET '
                       'manifest_sha256=excluded.manifest_sha256,checked_at=excluded.checked_at',
                       (row['key'], row['manifest_sha256'], stamp))
