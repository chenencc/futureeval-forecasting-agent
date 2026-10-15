"""Frozen cohorts and idempotent experiment attempts, isolated from native state."""
import json
from pathlib import Path

from .common import digest, encoded, get, instant, now, put, safe_metadata
from .contracts import validate
from .lifecycle import record, failure_code
from .trace import Trace


def implementation():
    import hashlib
    root = Path(__file__).parent
    return digest({p.name: hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                   for p in sorted(root.iterdir()) if p.suffix in ('.py', '.json')})


def environment():
    import importlib.metadata
    import platform
    import sqlite3
    return {'python': platform.python_version(), 'platform': platform.system(),
            'jsonschema': importlib.metadata.version('jsonschema'), 'sqlite': sqlite3.sqlite_version}


def freeze(db, name, packets, routes):
    if not name.strip() or not packets or not routes:
        raise ValueError('An experiment needs a name, explicit cases and routes')
    ids, route_ids = set(), set()
    for packet in packets:
        validate('packet', packet)
        if packet['case_id'] in ids:
            raise ValueError('Duplicate frozen case')
        ids.add(packet['case_id'])
    for route in routes:
        if set(route) != {'id', 'model', 'prompt', 'config'} or not all(isinstance(route[k], str) and route[k].strip() for k in ('id', 'model', 'prompt')):
            raise ValueError('Route requires ID, model, exact prompt and configuration')
        if route['id'] in route_ids:
            raise ValueError('Duplicate route ID')
        route_ids.add(route['id'])
        safe_metadata(route)
    manifest = {'schema': 'forecast-frozen-experiment-v1', 'name': name,
                'implementation_sha256': implementation(),
                'environment': environment(),
                'cases': sorted([{'id': p['case_id'], 'input_sha256': digest(p)} for p in packets], key=lambda p: p['id']),
                'routes': sorted(routes, key=lambda r: r['id']),
                'labels_in_model_input': False, 'native_tasks_modified': False}
    ident = digest(manifest)
    db.execute('BEGIN IMMEDIATE')
    try:
        previous = db.execute('SELECT id FROM experiments WHERE name=?', (name,)).fetchone()
        if previous and previous[0] != ident:
            raise ValueError('Experiment name already binds another immutable manifest')
        sha = put(db, manifest)
        db.execute('INSERT OR IGNORE INTO experiments VALUES(?,?,?,?)', (ident, name, sha, now()))
        for packet in packets:
            sha = put(db, packet)
            db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,?)', (ident, packet['case_id'], sha))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return ident


def manifest(db, ident):
    row = db.execute('SELECT manifest_sha256 FROM experiments WHERE id=?', (ident,)).fetchone()
    if not row:
        raise KeyError('Experiment is not registered')
    value = get(db, row[0])
    if digest(value) != ident:
        raise ValueError('Experiment manifest identity differs')
    return value


def event(case_id, state, code=None):
    return {'schema': 'forecast-lifecycle-event-v1', 'domain': 'experiment', 'task_key': case_id,
            'stage': 'analysis', 'state': state, 'occurred_at': now(), 'retry_at': None,
            'failure_code': code, 'source_sha256': digest([case_id, state]), 'native': False}


def run(db, ident, case_id, route_id, runner=None, *, audit_only=False):
    frozen = manifest(db, ident)
    if frozen['implementation_sha256'] != implementation():
        raise ValueError('Experiment implementation changed; freeze a new version')
    if not audit_only and frozen['environment'] != environment():
        raise ValueError('Experiment execution environment changed; freeze a new version')
    route = next((r for r in frozen['routes'] if r['id'] == route_id), None)
    if route is None:
        raise KeyError('Unknown frozen route')
    row = db.execute('SELECT input_sha256 FROM cases WHERE experiment_id=? AND case_id=?', (ident, case_id)).fetchone()
    if not row:
        raise KeyError('Case is not in the frozen cohort')
    packet, input_sha = get(db, row[0]), row[0]
    validate('packet', packet)
    mode = 'audit' if audit_only else 'prediction'
    run_id = digest([ident, case_id, route_id, mode])
    db.execute('BEGIN IMMEDIATE')
    try:
        prior = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
        if prior:
            db.rollback()
            return {'id': run_id, 'status': prior[0], 'reused': True, 'new_runner_calls': 0}
        db.execute('INSERT INTO runs(id,experiment_id,case_id,route_id,mode,status,started_at) VALUES(?,?,?,?,?,?,?)',
                   (run_id, ident, case_id, route_id, mode, 'running', now()))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    trace = Trace(db, 'experiment', run_id, ident, {'input_sha256': input_sha, 'route_id': route_id})
    record(db, event(run_id, 'queued'))
    record(db, event(run_id, 'running'))
    output_sha, runner_called = None, False
    try:
        with trace.span('analysis' if not audit_only else 'snapshot_validation') as span:
            if audit_only:
                output = {'schema': 'forecast-snapshot-audit-v1', 'case_id': case_id, 'input_sha256': input_sha,
                          'readable_sources': len(packet['sources']), 'gap_count': len(packet['gaps']),
                          'unknown_capture_clocks': sum(s['captured_at'] is None for s in packet['sources']),
                          'forecast_generated': False, 'provider_calls': 0}
                usage = {'http_attempts': 0, 'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0}
            else:
                if runner is None:
                    raise ValueError('A runner must be supplied explicitly')
                if not packet['sources']:
                    raise ValueError('No readable originals; preserve this gap before model execution')
                runner_called = True
                output, usage = runner(packet, route)
            with db:
                output_sha = put(db, output)
            if not audit_only:
                validate('forecast', output)
                if output['case_id'] != case_id or output['input_sha256'] != input_sha or output['kind'] != packet['question']['kind']:
                    raise ValueError('Runner output is not bound to the frozen input')
                if output['kind'] == 'multiple_choice' and set(output['value']) != set(packet['question']['options']):
                    raise ValueError('Runner options differ from the frozen question')
            validate('usage', usage)
            span['usage'] = usage
        status = 'validated' if audit_only else 'completed'
        with db:
            db.execute('UPDATE runs SET status=?,finished_at=?,output_sha256=?,usage=? WHERE id=?',
                       (status, now(), output_sha, encoded(usage).decode(), run_id))
        record(db, event(run_id, 'completed'))
    except Exception as exc:
        code = failure_code(exc)
        with db:
            db.execute('UPDATE runs SET status=?,finished_at=?,output_sha256=?,error_code=? WHERE id=?',
                       ('failed', now(), output_sha, code, run_id))
        record(db, event(run_id, 'blocked', code))
        return {'id': run_id, 'status': 'failed', 'error_code': code, 'new_runner_calls': int(runner_called)}
    return {'id': run_id, 'status': status, 'new_runner_calls': int(runner_called)}


def add_label(db, ident, case_id, kind, value, observed_at, *, source_sha256):
    import re
    if not re.fullmatch('[a-f0-9]{64}', source_sha256):
        raise ValueError('Labels require a recorded source hash')
    instant(observed_at)
    if kind not in ('resolved_binary', 'resolved_choice', 'market_price'):
        raise ValueError('Unknown label type')
    if kind == 'resolved_binary' and type(value) not in (int, float) or kind == 'resolved_binary' and value not in (0, 1):
        raise ValueError('Binary resolution must be zero or one')
    if kind == 'resolved_choice' and not isinstance(value, str):
        raise ValueError('Resolved option must be an exact string')
    if kind == 'market_price':
        if not isinstance(value, dict) or set(value) != {'value', 'target_at', 'origin_at', 'quote_at', 'source_sha256'}:
            raise ValueError('Price label requires exact target and quote provenance')
        if type(value['value']) not in (float, int) or not 0 <= value['value'] <= 1:
            raise ValueError('Invalid price label')
        for key in ('target_at', 'origin_at', 'quote_at'):
            instant(value[key])
        if instant(value['origin_at']) >= instant(value['target_at']) or instant(value['quote_at']) > instant(observed_at):
            raise ValueError('Invalid price label clocks')
    data = {'value': value, 'source_sha256': source_sha256}
    token = digest([ident, case_id, kind, observed_at, data])
    with db:
        db.execute('INSERT OR IGNORE INTO labels VALUES(?,?,?,?,?,?)', (token, ident, case_id, kind, observed_at, encoded(data).decode()))
    return token


def evaluate(db, ident):
    frozen = manifest(db, ident)
    results = {r['id']: {'expected_cases': len(frozen['cases']), 'not_started': len(frozen['cases']),
                        'completed': 0, 'failed': 0, 'incomplete': 0, 'audit_validated': 0, 'audit_failed': 0, 'scores': []} for r in frozen['routes']}
    for row in db.execute('SELECT * FROM runs WHERE experiment_id=?', (ident,)):
        group = results[row['route_id']]
        if row['mode'] == 'audit':
            if row['status'] in ('validated', 'failed'):
                group['audit_validated' if row['status'] == 'validated' else 'audit_failed'] += 1
        else:
            group['not_started'] -= 1
            group[row['status'] if row['status'] in ('completed', 'failed') else 'incomplete'] += 1
    # Every label is versioned. Multiple resolutions require explicit adjudication;
    # never silently choose whichever resolution was most recently imported.
    cases = list(db.execute('SELECT case_id FROM cases WHERE experiment_id=?', (ident,)))
    paired = []
    for case in cases:
        labels = list(db.execute('SELECT * FROM labels WHERE experiment_id=? AND case_id=?', (ident, case[0])))
        if len(labels) != 1 or labels[0]['kind'] not in ('resolved_binary', 'resolved_choice'):
            continue
        label = json.loads(labels[0]['data'])['value']
        scores = {}
        for row in db.execute('SELECT * FROM runs WHERE experiment_id=? AND case_id=? AND status=? AND mode=?', (ident, case[0], 'completed', 'prediction')):
            prediction = get(db, row['output_sha256'])
            if labels[0]['kind'] == 'resolved_binary' and prediction['kind'] == 'binary':
                score = (prediction['value'] - label)**2
            elif labels[0]['kind'] == 'resolved_choice' and prediction['kind'] == 'multiple_choice' and label in prediction['value']:
                score = sum((p - int(option == label))**2 for option, p in prediction['value'].items())
            else:
                continue
            results[row['route_id']]['scores'].append(score)
            scores[row['route_id']] = score
        if set(scores) == set(results):
            paired.append(scores)
    for route_id, group in results.items():
        scores = group.pop('scores')
        group['resolved_scored_cases'] = len(scores)
        group['brier'] = sum(scores) / len(scores) if scores else None
        group['paired_brier'] = sum(p[route_id] for p in paired) / len(paired) if paired else None
    return {'schema': 'forecast-experiment-evaluation-v1', 'experiment_id': ident, 'routes': results,
            'paired_cases': len(paired), 'labels_excluded_from_model_input': True,
            'market_price_labels_are_not_event_resolution': True,
            'forecast_skill_claimed': False, 'temporal_or_selection_validity_requires_separate_audit': True}
