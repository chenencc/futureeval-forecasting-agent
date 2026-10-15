"""Durable, scoped pipeline alerts; no research calls or automatic reruns."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from ForecastAgent.local_transfer import atomic_json
from ForecastAgent.pipeline_readiness import instant, sha


def age_minutes(stamp, now):
    parsed = instant(stamp)
    return max(0, (now-parsed).total_seconds()/60) if parsed else None


def observations(db, summary, source):
    db.execute('''CREATE TABLE IF NOT EXISTS pipeline_observations(
        id TEXT PRIMARY KEY,stream TEXT NOT NULL,run_id TEXT,observed_at TEXT NOT NULL,data TEXT NOT NULL)''')
    for content in summary.get('sources', {}).values():
        report = content.get('report.json') or content.get('monitor-health.json')
        if not isinstance(report, dict):
            continue
        # Provider raw records and model prompts never enter health notifications.
        allowed = ('discovery_complete', 'discovery_coverage', 'intake_health', 'errors_count',
                   'pending', 'state_distribution', 'healthy', 'open_scan_complete', 'open_question_count')
        report = {k: report[k] for k in allowed if k in report}
        if report:
            value = {'source': source, 'observed_at': summary['generated_at'], 'report': report}
            db.execute('INSERT OR IGNORE INTO pipeline_observations VALUES(?,?,?,?,?)',
                       (sha(value), source.get('stream', 'unknown'), str(source.get('run_id', '')),
                        summary['generated_at'], json.dumps(report, sort_keys=True)))


def alerts(db, domain, now=None):
    now = now or datetime.now(timezone.utc)
    result = []
    def add(code, key='', severity='warning', **details):
        result.append({'id': sha([domain, code, key]), 'domain': domain, 'code': code,
                       'key': key, 'severity': severity, **details})
    if domain == 'market':
        for row in db.execute('SELECT market_key,data FROM latest_tasks'):
            task = json.loads(row['data']); state = task.get('status')
            age = age_minutes(task.get('enrolled_at'), now)
            if state in {'blocked', 'failed', 'rules_changed', 'expired_anchor'}:
                add('task_requires_attention', row['market_key'], status=state)
            if state in {'queued', 'running', 'retry_wait', 'budget_wait'}:
                deadline = instant(task.get('retry_at'))
                if deadline and deadline > now:
                    continue
                if age is not None and age > 30:
                    add('admitted_task_overdue', row['market_key'], age_minutes=round(age, 2), status=state)
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_observations'").fetchone()
        row = db.execute("SELECT * FROM pipeline_observations WHERE stream='market-monitor' "
                         'ORDER BY observed_at DESC,rowid DESC LIMIT 1').fetchone() if exists else None
        if row:
            report = json.loads(row['data'])
            for venue, coverage in report.get('discovery_coverage', {}).items():
                lag = coverage.get('coverage_lag_seconds')
                if isinstance(lag, (float, int)) and lag > 1800:
                    add('discovery_coverage_lag', venue, lag_seconds=lag, complete=coverage.get('complete'))
                if coverage.get('errors'):
                    add('discovery_page_failure', venue, errors=coverage['errors'])
                if coverage.get('historical_gap_count'):
                    add('historical_discovery_gap', venue, count=coverage['historical_gap_count'])
            for alert in report.get('intake_health', {}).get('alerts', []):
                add('intake_'+str(alert.get('kind', 'attention')), alert.get('key', ''), status=alert.get('status'))
        elif db.execute('SELECT 1 FROM markets LIMIT 1').fetchone():
            add('discovery_health_not_recorded')
    else:
        for row in db.execute('SELECT question_key,stage,data FROM latest_tasks'):
            if row['stage'] in {'blocked', 'failed', 'submission_uncertain', 'unsupported'}:
                add('task_requires_attention', row['question_key'], status=row['stage'])
            if row['stage'] in {'queued', 'running', 'retry_wait'}:
                task = json.loads(row['data'])
                deadline = instant(task.get('retry_at') or task.get('retry_at_utc'))
                if deadline and deadline > now:
                    continue
                age = age_minutes(task.get('enrolled_at') or task.get('created_at_utc'), now)
                if age is not None and age > 30:
                    add('admitted_task_overdue', row['question_key'], age_minutes=round(age, 2))
    return result


def persist(db, items):
    stamp = datetime.now(timezone.utc).isoformat()
    db.execute('''CREATE TABLE IF NOT EXISTS pipeline_alerts(
        id TEXT PRIMARY KEY,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
        resolved_at TEXT,data TEXT NOT NULL)''')
    ids = {a['id'] for a in items}
    with db:
        for item in items:
            db.execute('INSERT INTO pipeline_alerts VALUES(?,?,?,NULL,?) ON CONFLICT(id) DO UPDATE SET '
                       'last_seen_at=excluded.last_seen_at,resolved_at=NULL,data=excluded.data',
                       (item['id'], stamp, stamp, json.dumps(item, sort_keys=True)))
        for row in db.execute('SELECT id FROM pipeline_alerts WHERE resolved_at IS NULL').fetchall():
            if row[0] not in ids:
                db.execute('UPDATE pipeline_alerts SET resolved_at=? WHERE id=?', (stamp, row[0]))


def replay_alerts(report, domain):
    return [{'id':sha([domain,'snapshot_not_replay_ready',r['key']]),'domain':domain,
             'code':'snapshot_not_replay_ready','key':r['key'],'severity':'warning','gaps':r['gaps']}
            for r in report['tasks'] if not r['replay_ready'] and
            r['processing_status'] in {'completed','accepted'}]


def observe_triggers(root, client):
    """Fetch only workflow metadata; a dispatch receipt is never completion proof."""
    stamp = datetime.now(timezone.utc)
    result = {'observed_at': stamp.isoformat(), 'runs': {}, 'alerts': []}
    for lane, repo, workflow, maximum_age in (
        ('competition_listener', 'chenencc/futureeval-monitor', 'monitor.yaml', 25),
        ('market_listener', 'chenencc/futureeval-forecasting-agent', 'market_research_monitor.yaml', 25),
        ('price_history', 'chenencc/futureeval-forecasting-agent', 'market_price_history.yaml', 180)):
        try:
            runs = client.api(f'repos/{repo}/actions/workflows/{workflow}/runs?branch=main&per_page=5')['workflow_runs']
            if not runs:
                raise ValueError('No workflow receipts')
            head = runs[0]
            if head.get('path') != '.github/workflows/'+workflow or head.get('head_branch') != 'main':
                raise ValueError('Workflow metadata identity differs')
            latest_success = next((r for r in runs if r.get('conclusion') == 'success'), None)
            result['runs'][lane] = {k: head.get(k) for k in ('id', 'created_at', 'updated_at', 'status', 'conclusion', 'html_url')}
            result['runs'][lane]['latest_success_at'] = latest_success.get('updated_at') if latest_success else None
            age = age_minutes(head.get('created_at'), stamp)
            if age is None or age > maximum_age:
                result['alerts'].append({'domain': lane, 'code': 'no_recent_dispatch', 'age_minutes': age})
            success_age=age_minutes(latest_success.get('updated_at'),stamp) if latest_success else None
            if success_age is not None and success_age>maximum_age:
                result['alerts'].append({'domain':lane,'code':'no_recent_completion','age_minutes':success_age})
            if head.get('status') == 'completed' and head.get('conclusion') != 'success':
                result['alerts'].append({'domain': lane, 'code': 'latest_workflow_failed', 'run_id': head['id']})
            elif not latest_success and head.get('status') != 'completed':
                result['alerts'].append({'domain': lane, 'code': 'completion_not_yet_verified'})
        except Exception as exc:
            result['alerts'].append({'domain': lane, 'code': 'trigger_observation_failed',
                                     'error_type': type(exc).__name__, 'reason': str(exc)[:180]})
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    for item in result['alerts']:
        item.update(id=sha([item['domain'],item['code']]),severity='warning')
    control=sqlite3.connect(root/'pipeline-control.sqlite3')
    try:
        control.execute('CREATE TABLE IF NOT EXISTS trigger_observations(id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,data TEXT NOT NULL)')
        with control:
            control.execute('INSERT OR IGNORE INTO trigger_observations VALUES(?,?,?)',
                            (sha(result),result['observed_at'],json.dumps(result,sort_keys=True)))
        persist(control,result['alerts'])
    finally:control.close()
    atomic_json(root/'pipeline-trigger-status.json', result)
    return result
