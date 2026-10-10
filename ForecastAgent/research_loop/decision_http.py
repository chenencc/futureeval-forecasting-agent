"""Experimental decision transport; frozen release provider stays unchanged."""
import json
import math
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from ForecastAgent.providers.decisions import MODEL, ENDPOINT, validate
from ForecastAgent.analysis.pilot import digest, load, save, Journal

def response_headers(headers):
    """Keep useful limit metadata only; never record authentication headers."""
    names = ('retry-after', 'date', 'x-request-id', 'x-ratelimit-limit',
             'x-ratelimit-remaining', 'x-ratelimit-reset')
    return {name: str(headers.get(name))[:200] for name in names if headers and headers.get(name) is not None}


def failure_metadata(code, headers):
    """A 429 alone cannot distinguish a short rate limit from exhausted quota."""
    value = headers.get('retry-after')
    seconds = None
    if value:
        try:
            if value.isdigit():
                seconds = int(value)
            else:
                deadline = parsedate_to_datetime(value)
                if deadline.tzinfo is not None:
                    seconds = max(0, math.ceil((deadline - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            pass
    return {'kind': 'rate_or_quota_limit' if code == 429 else 'service_failure' if code >= 500 else 'request_or_account_error',
        'retry_after_seconds': seconds, 'retry_deadline_known': seconds is not None,
        'automatic_retry': False, 'quota_reset': False}


def decide(state, questions, api_key, observer, *, required_questions=None):
    payload = {'model': MODEL, 'state': state, 'questions': questions}
    record = {'endpoint': ENDPOINT, 'request': payload, 'status': 'reserved'}
    token = observer('reserve', record)
    request = Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json',
        'X-Title': 'ForecastAgent isolated analysis'}, method='POST')
    try:
        with urlopen(request, timeout=120) as response:
            record.update(http_status=response.status, response_body=response.read().decode(),
                response_headers=response_headers(response.headers))
        record['response'] = json.loads(record['response_body'])
        validate(record['response'], required_questions if required_questions is not None else questions)
        record['status'] = 'received'
    except HTTPError as exc:
        headers = response_headers(exc.headers)
        record.update(status='http_error', http_status=exc.code, response_body=exc.read().decode(errors='replace'),
            response_headers=headers, failure=failure_metadata(exc.code, headers))
        raise RuntimeError('Decision endpoint HTTP ' + str(exc.code)) from exc
    except Exception as exc:
        record.update(status='invalid_or_transport_error', error=str(exc))
        raise
    finally:
        observer('complete', record, token)
    return record['response']


def call(state, folder, registry, *, required_questions=None):
    """Same exact cache and physical cap; additional response metadata only."""
    request = {'model': MODEL, 'state': state, 'questions': registry}
    identity = {'request_sha256': digest(request)}
    if required_questions is not None:
        if not required_questions or any(registry.get(k) != v for k, v in required_questions.items()):
            raise ValueError('Required decision heads must be an exact nonempty request subset')
        identity['required_questions_sha256'] = digest(required_questions)
    if (folder/'identity.json').exists() and load(folder/'identity.json') != identity:
        raise ValueError('Frozen decision stage changed')
    save(folder/'identity.json', identity); save(folder/'request.json', request)
    if (folder/'response.json').exists():
        return validate(load(folder/'response.json'), required_questions if required_questions is not None else registry)
    response = decide(state, registry, os.environ['OPENROUTER_API_KEY'], Journal(folder/'http', 1), required_questions=required_questions)
    save(folder/'response.json', response)
    return response
