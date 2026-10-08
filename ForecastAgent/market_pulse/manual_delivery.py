"""Explicitly authorized, frozen financial forecasts -> confirmed Metaculus receipts.

This one-off operator entry point does not run collection, analysis, scheduling
or forecasting updates. Reuse the existing atomic forecast/private-comment
transport and its unknown-outcome protection. All four candidates are checked
before any POST, with fresh rules and another check immediately before delivery.
"""
import argparse
import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.competition.platform import Client, NoRedirect, deliver, has_existing_forecast, forecast_matches
from ForecastAgent.competition.queue import questions, utc
from ForecastAgent.market_pulse import inventory, analysis
from ForecastAgent.releases.v1_0_5 import validate_payload, verify_release
from ForecastAgent.runtime.task_lock import task_lock

RULE_FIELDS = ('question', 'background', 'resolution_criteria', 'fine_print', 'unit',
    'question_type', 'scaling', 'inbound_outcome_count', 'open_lower_bound', 'open_upper_bound')


class PacedClient(Client):
    """Bounded GET retries respect server backoff; POST is never auto-retried."""
    def __init__(self, token, archive, interval=8):
        super().__init__(token)
        self.archive = Path(archive); self.interval = interval
        self.next_request_at = 0; self.post_cache = {}

    def _pace(self):
        while self.next_request_at > time.monotonic():
            time.sleep(max(0, min(15, self.next_request_at - time.monotonic())))
        self.next_request_at = time.monotonic() + self.interval

    def _send(self, method, path, data):
        if method == 'GET' and path == f'projects/tournaments/{inventory.SLUG}/':
            url = 'https://www.metaculus.com/api/' + path
            req = Request(url, headers={'Authorization': 'Token ' + self.token,
                'Accept': 'application/json', 'User-Agent': 'ForecastAgent-MarketPulse/1.0'})
            with build_opener(NoRedirect()).open(req, timeout=60) as response:
                return {'status': response.status, 'data': json.load(response)}
        return super().request(method, path, data)

    def request(self, method, path, data=None):
        if method == 'POST':
            self.post_cache.clear()
        for attempt in range(3 if method == 'GET' else 1):
            self._pace()
            try:
                return self._send(method, path, data)
            except HTTPError as exc:
                if method != 'GET' or exc.code != 429 or attempt == 2:
                    raise
                raw = exc.headers.get('Retry-After') if exc.headers else None
                try:
                    delay = float(raw) if raw is not None else 60
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(raw) - utc()).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        delay = 60
                delay = max(1, delay)
                self.next_request_at = max(self.next_request_at, time.monotonic() + delay)
                record = {'method': 'GET', 'path': path, 'http_status': 429,
                    'retry_after_seconds': delay, 'attempt': attempt + 1,
                    'at_utc': utc().isoformat(), 'post_retried': False}
                save(self.archive / f'{time.time_ns()}.json', record)
                print(json.dumps({'platform_read_backoff_seconds': delay, 'path': path}), flush=True)

    def post(self, ident):
        key = str(ident)
        cached = self.post_cache.get(key)
        if cached and time.monotonic() - cached[0] <= 2:
            return copy.deepcopy(cached[1])
        value = super().post(ident)
        self.post_cache[key] = (time.monotonic(), copy.deepcopy(value))
        return value


def metadata(client):
    return client.request('GET', f'projects/tournaments/{inventory.SLUG}/')['data']


def format_quantile(value, metric, request):
    raw = value.get('value'); factor = 1e9 if metric == 'quarterly_revenue' else 1
    unit = 'USD billion' if factor != 1 else 'USD/share'
    if raw is not None:
        return f'{raw / factor:.4f} {unit}'
    state = value.get('state', '')
    if 'below' in state:
        return f'below {request["scaling"]["range_min"] / factor:g} {unit} (open tail)'
    if 'above' in state:
        return f'above {request["scaling"]["range_max"] / factor:g} {unit} (open tail)'
    raise ValueError('Unknown open-tail quantile representation')


def prepare(root, inputs_path, source_plan, commit):
    """Freeze the exact previously generated candidate; never recalculate a model."""
    items = {i['id']: i for i in load(inputs_path)}; entries = []
    for ident, paths in source_plan.items():
        item = items[ident]; bundle = load(item['package'])
        audit_path, payload_path = map(Path, paths)
        audit = next(q for q in load(audit_path)['questions'] if q['id'] == ident)
        row = next(q for q in load(payload_path)['questions'] if q['id'] == ident)
        candidate = row['payload']; validate_payload(bundle['request'], candidate)
        if audit.get('cdf_format_valid') is not True:
            raise ValueError('Independent format audit required')
        financial = analysis.contract(bundle['request'])
        qs = audit['independent_quantiles']
        sources = list(dict.fromkeys(v['original_row_ref']['url'] for v in load(item['variables'])
            if not v['original_row_ref'].get('field')))
        facts_note = ''
        if audit.get('added_facts'):
            sources += [v['original_row_ref']['url'] for v in audit['added_facts']]
            facts_note = ('The latest saved prior-quarter release is included, together with its original '
                'reported EPS and disclosed refund/one-off effects. No one-off component is '
                'automatically subtracted from reported GAAP EPS or assumed to recur.\n\n')
        comment = ('# ForecastAgent — Market Pulse 26Q4\n\n'
            f'## Target\n{financial["issuer"]}: {financial["metric"]}, {financial["target_period"]}. '
            'Forecast the first official release under the original question rules.\n\n'
            '## Predictive distribution\n' + '\n'.join(f'- {name}: {format_quantile(qs[p], financial["metric"], bundle["request"])}'
                for name, p in [('10th percentile', '0.1'), ('Median', '0.5'), ('90th percentile', '0.9')])
            + '\n\n## Method and uncertainty\n'
            'Independent Mercury outcome distribution from saved original financial evidence. '
            'Super provides a separate source-bound projection; it is not averaged into this submission. '
            'Fiscal versus calendar periods, quarterly versus cumulative columns, currency/share units '
            'and reported GAAP versus adjusted quantities are kept distinct. Management guidance '
            'is a predictor, not a probability interval. Future margins, tax, one-off recurrence, '
            'shares and forecast-error coverage remain uncertain; probability calibration is not yet validated.\n\n'
            + facts_note + '## Original sources\n' + '\n'.join('- ' + u for u in dict.fromkeys(sources))
            + f'\n\nModel generation was previously archived. Development commit: {commit}. '
            'Only the format-validated independent distribution is delivered; open tails are retained. '
            'CDF probabilities are bounded to [0.02, 0.98]; financial values are not clipped.')
        entries.append({'id': ident, 'package': item['package'],
            'package_sha256': analysis.sha(item['package']),
            'audit_path': str(audit_path), 'audit_sha256': analysis.sha(audit_path),
            'payload_path': str(payload_path), 'payload_file_sha256': analysis.sha(payload_path),
            'candidate': candidate, 'candidate_sha256': digest(candidate), 'comment': comment,
            'issuer': financial['issuer'], 'metric': financial['metric'],
            'target_period': financial['target_period'], 'quantiles': qs})
    plan = {'schema': 'market-pulse-manual-delivery-v1', 'tournament': inventory.SLUG,
        'project_id': inventory.PROJECT_ID, 'source_commit': commit, 'entries': entries,
        'authorization': 'User explicitly authorized submission of these four latest saved forecasts on 2026-10-09.',
        'model_calls': 0, 'search_calls': 0, 'production_changes': False}
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'plan.json').exists() and load(root / 'plan.json') != plan:
        raise ValueError('Frozen authorized plan changed')
    save(root / 'plan.json', plan)
    print(json.dumps({'prepared': [e['id'] for e in entries], 'root': str(root), 'submitted': False}))


def compare(entry, live, row, question):
    for field, key in [('package', 'package_sha256'), ('audit_path', 'audit_sha256'),
                        ('payload_path', 'payload_file_sha256')]:
        if analysis.sha(entry[field]) != entry[key]:
            raise ValueError('Frozen source/report changed: ' + entry['id'])
    saved = load(entry['package'])['request']
    differences = [field for field in RULE_FIELDS if digest(saved.get(field)) != digest(live.get(field))]
    if differences:
        raise ValueError('Official rules/metadata changed: ' + entry['id'] + ': ' + ', '.join(differences))
    validate_payload(live, entry['candidate'])
    if digest(entry['candidate']) != entry['candidate_sha256']:
        raise ValueError('Frozen forecast changed')
    if row['rule_review'] or not row['automatic_candidate']:
        raise ValueError('Fresh eligibility requires review: ' + entry['id'])
    return {'id': entry['id'], 'post_id': row['post_id'], 'status': row['status'],
        'permission': row['permission'], 'deadline_utc': row['conservative_deadline_utc'],
        'rules_and_scale_unchanged': True, 'payload_format_valid': True,
        'existing_forecast': has_existing_forecast(question),
        'existing_forecast_matches': forecast_matches(question, entry['candidate']),
        'url': row['url']}


def run(root, snapshot_path, execute=False):
    plan = load(root / 'plan.json')
    if plan['tournament'] != inventory.SLUG or plan['project_id'] != inventory.PROJECT_ID:
        raise ValueError('Wrong authorized tournament')
    if len(plan['entries']) != 4 or len({e['id'] for e in plan['entries']}) != 4:
        raise ValueError('This authorized operation requires exactly four unique leaves')
    verify_release(); client = PacedClient(os.environ['METACULUS_TOKEN'], root / 'read-backoff')
    with task_lock(root):
        current = root / 'preflight' / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')
        current.mkdir(parents=True, exist_ok=False)
        account = client.account(); save(current / 'account.json', account)
        meta = metadata(client); save(current / 'metadata.json', meta)
        if meta.get('bot_leaderboard_status') != 'include' or 'Bot Participation Rules' not in meta.get('description', ''):
            raise ValueError('Fresh bot participation rules require review')
        old_inventory = load(snapshot_path / 'inventory.json')
        parents = {r['post_id'] for r in old_inventory['rows']
            if r['question_id'] in {e['id'] for e in plan['entries']}}
        posts = [client.post(p) for p in sorted(parents)]
        for post in posts:
            save(current / f'post-{post["id"]}.json', post)
        inspected, live_inputs = inventory.inspect(meta, posts, utc().isoformat())
        save(current / 'inventory.json', inspected)
        checked = []
        for entry in plan['entries']:
            row = next(r for r in inspected['rows'] if r['question_id'] == entry['id'])
            post = next(p for p in posts if p['id'] == row['post_id'])
            question = next(q for q in questions(post) if str(q['id']) == entry['id'])
            checked.append(compare(entry, live_inputs[entry['id']]['request'], row, question))
        save(current / 'report.json', {'checked_at_utc': utc().isoformat(), 'account': account,
            'rows': checked, 'format_and_live_eligibility_valid': True, 'submitted': False})
        print(json.dumps({'fresh_preflight': checked, 'execute': execute}), flush=True)
        if not execute:
            return
        results = []
        for entry in plan['entries']:
            check = next(c for c in checked if c['id'] == entry['id'])
            task = {'id': entry['id'], 'post_id': check['post_id'],
                'analysis_commit': plan['source_commit'],
                'analysis_release_version': 'market-pulse-compact-source-bound-v1'}
            try:
                # Recheck full rules immediately before the existing transport
                # makes its own deadline/permission/history check and reserves POST.
                post = client.post(task['post_id'])
                inventory_now, inputs_now = inventory.inspect(meta, [post], utc().isoformat())
                row = next(r for r in inventory_now['rows'] if r['question_id'] == entry['id'])
                q = next(q for q in questions(post) if str(q['id']) == entry['id'])
                compare(entry, inputs_now[entry['id']]['request'], row, q)
                receipt = deliver(client, task, entry['candidate'], entry['comment'],
                    root / 'tasks' / entry['id'], enabled=True)
                result = {'id': entry['id'], 'issuer': entry['issuer'], 'url': check['url'],
                    'receipt': receipt, 'quantiles': entry['quantiles']}
            except Exception as exc:
                result = {'id': entry['id'], 'issuer': entry['issuer'],
                    'status': 'needs_review', 'error_type': type(exc).__name__, 'error': str(exc),
                    'state_preserved': True, 'no_automatic_duplicate_post': True}
            results.append(result)
            save(root / 'progress.json', {'results': results})
            print(json.dumps({'id': entry['id'], 'status': result.get('receipt', {}).get('status', result.get('status')),
                'error': result.get('error')}), flush=True)
        # Fresh batch readback, rather than trusting local accepted flags alone.
        final_posts = {p: client.post(p) for p in sorted(parents)}
        verified = []
        for entry in plan['entries']:
            check = next(c for c in checked if c['id'] == entry['id'])
            post = final_posts[check['post_id']]; save(current / f'final-post-{post["id"]}.json', post)
            q = next(q for q in questions(post) if str(q['id']) == entry['id'])
            verified.append({'id': entry['id'], 'payload_matches_authenticated_platform': forecast_matches(q, entry['candidate']),
                'own_forecast_present': has_existing_forecast(q),
                'forecast_start_time': ((q.get('my_forecasts') or {}).get('latest') or {}).get('start_time')})
        report = {'schema': 'market-pulse-manual-submission-report-v1', 'completed_at_utc': utc().isoformat(),
            'account': account, 'plan_sha256': analysis.sha(root / 'plan.json'),
            'preflight': str(current), 'results': results, 'final_readback': verified,
            'confirmed_forecasts': sum(v['payload_matches_authenticated_platform'] for v in verified),
            'new_model_calls': 0, 'new_search_calls': 0, 'production_changes': False}
        save(root / 'report.json', report)
        print(json.dumps({'confirmed_forecasts': report['confirmed_forecasts'], 'report': str(root / 'report.json')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    run(args.root, args.snapshot, args.execute)
