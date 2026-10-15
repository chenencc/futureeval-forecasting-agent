"""Authenticated Metaculus transport and atomic, readback-confirmed delivery."""
import json
import math
import hashlib
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ForecastAgent.competition.queue import descriptor, digest, load, save, questions, utc

HOST = 'https://www.metaculus.com/api/'
BOT_ID = 309450
MAX_POST_ATTEMPTS = 3
RETRY_SERVICE_CODES = {502, 503, 504}
NEGATIVE_READBACK_INTERVAL_SECONDS = 30
POST_RETRY_DELAY_SECONDS = 120


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, token):
        if not token:
            raise ValueError('METACULUS_TOKEN is required')
        self.token = token

    def request(self, method, path, data=None):
        if method == 'GET':
            allowed = path.startswith(('users/me/', 'posts/', 'comments/?'))
        else:
            allowed = method == 'POST' and path == 'questions/bulk-forecast-comment/'
        if not allowed or '://' in path or '..' in path:
            raise ValueError('Unapproved platform endpoint')
        body = json.dumps(data).encode() if data is not None else None
        request = Request(HOST + path, data=body, method=method, headers={
            'Authorization': 'Token ' + self.token, 'Accept': 'application/json',
            'Content-Type': 'application/json', 'User-Agent': 'ForecastAgent-official/1.0.1'})
        with build_opener(NoRedirect()).open(request, timeout=60) as response:
            raw = response.read()
            return {'status': response.status, 'data': json.loads(raw) if raw else {}}

    def account(self):
        data = self.request('GET', 'users/me/')['data']
        result = {k: data.get(k) for k in ('id', 'username', 'is_bot', 'is_active', 'api_forecasting_access')}
        if result['id'] != BOT_ID or result['is_bot'] is not True or result['is_active'] is not True or result['api_forecasting_access'] != 'enabled':
            raise ValueError('Current credential is not the authorized enabled competition bot')
        return result

    def post(self, ident):
        if not str(ident).isdecimal():
            raise ValueError('Numeric post ID required')
        # User forecast history is only populated when with_cp is enabled.
        # Community forecasts are never copied into the model's live request.
        return self.request('GET', f'posts/{ident}/?include_descriptions=true&with_cp=true')['data']

    def comments(self, post_id):
        path = 'comments/?' + urlencode({'post': int(post_id), 'author': BOT_ID, 'is_private': 'true', 'limit': 100})
        result = []
        for page in range(20):
            data = self.request('GET', path + f'&offset={page * 100}')['data']
            rows = data.get('results', []) if isinstance(data, dict) else data
            if not isinstance(rows, list):
                raise ValueError('Unexpected comment readback response')
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError('Private comment readback pagination incomplete')


def has_existing_forecast(question):
    """Fail closed when authenticated forecast history cannot be inspected."""
    own = question.get('my_forecasts')
    if not isinstance(own, dict) or 'latest' not in own or not isinstance(own.get('history'), list):
        raise RuntimeError('Own forecast history unreadable; refuse acquisition or duplicate submission')
    rows = own['history'] + ([own['latest']] if own['latest'] is not None else [])
    for row in rows:
        if not isinstance(row, dict) or row.get('author_id') != BOT_ID:
            raise RuntimeError('Own forecast history identity unreadable; refuse duplicate submission')
    return bool(rows)


def forecast_matches(question, candidate):
    latest = (question.get('my_forecasts') or {}).get('latest') or {}
    if latest.get('author_id') != BOT_ID:
        return False
    values = latest.get('forecast_values')
    if 'probability_yes' in candidate:
        expected = [1 - candidate['probability_yes'], candidate['probability_yes']]
    elif 'continuous_cdf' in candidate:
        expected = candidate['continuous_cdf']
    else:
        options = question.get('all_options_ever') or question['options']
        expected = [candidate['probability_yes_per_category'].get(o) for o in options]
    if not isinstance(values, list) or len(values) != len(expected):
        return False
    return all((a is None and b is None) or (type(a) in (int, float) and type(b) in (int, float) and
        math.isfinite(a) and math.isfinite(b) and abs(a - b) <= 1e-8) for a, b in zip(values, expected))


def reconcile(client, task, record):
    post = client.post(task['post_id'])
    selected = next((q for q in questions(post) if str(q['id']) == task['id']), None)
    marker = record['marker']
    matches = [c for c in client.comments(task['post_id']) if marker in (c.get('text') or '') and
        (c.get('author') or {}).get('id') == BOT_ID and c.get('is_private') is True]
    matched = bool(selected and forecast_matches(selected, record['payload']))
    own_present = True if matched else has_existing_forecast(selected) if selected else None
    negative = bool(selected and own_present is False and not matches)
    record.setdefault('readback_checks', []).append({'at_utc': utc().isoformat(),
        'question_id': selected.get('id') if selected else None,
        'own_forecast_present': own_present, 'forecast_matches': matched,
        'matching_comment_ids': [c['id'] for c in matches],
        'complete_authenticated_negative': negative})
    if matched and matches:
        record.update(status='accepted', confirmed_at_utc=utc().isoformat(),
            comment_id=matches[0]['id'], forecast_start_time=selected['my_forecasts']['latest'].get('start_time'))
        return True
    return False


def retry_ready(client, task, record):
    """Retry known transient service failures only after separated negative reads."""
    if record.get('http_status') not in RETRY_SERVICE_CODES or record.get('acknowledged_at_utc'):
        return False
    attempts = record.get('post_attempts', [])
    if len(attempts) >= MAX_POST_ATTEMPTS:
        return False
    sent_at = attempts[-1]['reserved_at_utc'] if attempts else record['reserved_at_utc']
    checks = [c for c in record.get('readback_checks', []) if utc(c['at_utc']) > utc(sent_at)]
    if len(checks) < 2 or not all(c['complete_authenticated_negative'] for c in checks[-2:]):
        return False
    now = utc()
    if ((now-utc(sent_at)).total_seconds() < POST_RETRY_DELAY_SECONDS or
        (utc(checks[-1]['at_utc'])-utc(checks[-2]['at_utc'])).total_seconds() < NEGATIVE_READBACK_INTERVAL_SECONDS):
        return False
    client.account()
    post = client.post(task['post_id'])
    question = next((q for q in questions(post) if str(q['id']) == task['id']), None)
    if not question or post.get('user_permission') not in {'forecaster', 'curator', 'admin', 'creator'}:
        raise RuntimeError('Retry forecasting permission unavailable')
    deadline = descriptor(post, question)['deadline_utc']
    if question.get('status') != 'open' or not deadline or utc(deadline) <= now:
        raise RuntimeError('Retry forecasting deadline has passed')
    if has_existing_forecast(question):
        return False
    return True


def http_diagnostic(error, token=None):
    """Bound diagnostics without persisting credentials or authentication headers."""
    raw = error.read(8193)
    preview = raw[:8192].decode('utf-8', errors='replace')
    if token:
        preview = preview.replace(token, '[credential redacted]')
    return {'response_preview': preview, 'preview_truncated': len(raw)>8192,
        'captured_response_sha256': hashlib.sha256(raw[:8192]).hexdigest(),
        'headers': {name: error.headers.get(name) for name in
            ('Date', 'Content-Type', 'Retry-After', 'X-Request-ID', 'CF-Ray') if error.headers and error.headers.get(name)}}


def deliver(client, task, candidate, comment, folder, *, enabled=False):
    """Reconcile first; bounded retries require complete negative platform evidence."""
    if not enabled:
        raise ValueError('Competition delivery is not enabled')
    if candidate.get('question') != int(task['id']) or not comment.strip():
        raise ValueError('Submission identity or private reasoning missing')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'submission.json'
    marker = f'ForecastAgent receipt {digest({"question": task["id"], "payload": candidate})}'
    expected = {'payload': candidate, 'marker': marker, 'comment': comment + '\n\n' + marker}
    if path.exists():
        record = load(path)
        if any(record.get(k) != v for k, v in expected.items()):
            raise ValueError('Frozen submission payload/comment changed')
        if record['status'] == 'accepted':
            return record
        initial = folder / 'submission-initial.json'
        if not initial.exists():
            initial.write_bytes(path.read_bytes())
        if 'post_attempts' not in record:
            record['post_attempts'] = [{k: record[k] for k in
                ('reserved_at_utc', 'status', 'http_status', 'error', 'acknowledged_at_utc') if k in record}]
        try:
            confirmed = reconcile(client, task, record)
        except Exception as exc:
            record.setdefault('readback_failures', []).append({'at_utc': utc().isoformat(),
                'error_type': type(exc).__name__, 'http_status': getattr(exc, 'code', None)})
            save(path, record)
            raise
        save(path, record)
        if confirmed:
            save(path, record)
            return record
        ready = False
        if record['status'] in ('reserved', 'unknown', 'confirming'):
            try:
                ready = retry_ready(client, task, record)
            except Exception:
                save(path, record)
                raise
        if record['status'] in ('reserved', 'unknown', 'confirming') and not ready:
            record.update(status='unknown', last_checked_at_utc=utc().isoformat())
            save(path, record)
            raise RuntimeError('Submission outcome unknown; preserved record forbids duplicate POST')
        if record['status'] not in ('reserved', 'unknown', 'confirming'):
            save(path, record)
            raise RuntimeError('Platform rejected this frozen submission; operator review required')
    else:
        post = client.post(task['post_id'])
        question = next((q for q in questions(post) if str(q['id']) == task['id']), None)
        if not question or question.get('status') != 'open' or post.get('user_permission') not in ('forecaster', 'curator', 'admin', 'creator'):
            raise RuntimeError('Question is closed or forecasting permission unavailable')
        deadline = descriptor(post, question)['deadline_utc']
        if not deadline or utc(deadline) <= utc():
            raise RuntimeError('Forecasting deadline has passed')
        if has_existing_forecast(question):
            return {'status': 'already_forecasted', 'checked_at_utc': utc().isoformat(),
                'reason': 'Authenticated platform forecast exists; no new POST'}
        record = {'schema': 'official-submission-v1', **expected, 'status': 'reserved',
            'reserved_at_utc': utc().isoformat(), 'commit': task.get('analysis_commit',task.get('commit')), 'release_version':task.get('analysis_release_version',task.get('release_version')), 'method': 'POST',
            'endpoint': 'questions/bulk-forecast-comment/', 'atomic_forecast_and_comment': True,
            'post_attempts': []}
    attempt = {'reserved_at_utc': utc().isoformat(), 'status': 'reserved',
        'payload_sha256': digest(candidate), 'comment_sha256': digest(record['comment']),
        'delivery_commit': os.environ.get('FORECAST_RELEASE_COMMIT')}
    record['post_attempts'].append(attempt)
    record['status'] = 'reserved'
    save(path, record)
    body = {'user_id': BOT_ID, 'forecasts': [candidate], 'comments': [{'on_post': int(task['post_id']),
        'text': record['comment'], 'parent': None, 'is_private': True, 'included_forecast': True}]}
    try:
        response = client.request('POST', 'questions/bulk-forecast-comment/', body)
        if response['status'] != 201:
            raise RuntimeError('Unexpected forecast/comment acknowledgment')
        record.update(status='confirming', http_status=response['status'], acknowledged_at_utc=utc().isoformat())
        attempt.update(status='confirming', http_status=response['status'], acknowledged_at_utc=record['acknowledged_at_utc'])
    except HTTPError as exc:
        record.update(status='rejected' if 400 <= exc.code < 500 else 'unknown', http_status=exc.code,
            error='Platform HTTP ' + str(exc.code))
        attempt.update(status=record['status'], http_status=exc.code, error=record['error'],
            diagnostic=http_diagnostic(exc, getattr(client, 'token', None)))
        save(path, record)
        raise RuntimeError(record['error']) from exc
    except Exception as exc:
        record.update(status='unknown', error=type(exc).__name__)
        record.pop('http_status', None)
        attempt.update(status='unknown', error=type(exc).__name__)
        save(path, record)
        raise
    save(path, record)
    if not reconcile(client, task, record):
        save(path, record)
        raise RuntimeError('Acknowledged submission awaiting forecast and private comment readback')
    save(path, record)
    return record
