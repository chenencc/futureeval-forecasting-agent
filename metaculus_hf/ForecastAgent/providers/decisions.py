"""Audited System One decisions; never route to a paid model."""
import json
import math
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODEL = 'inception/mercury-decide:free'
ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'


def probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Invalid probability')
    return float(value)


def validate(response, questions):
    if not isinstance(response, dict) or not str(response.get('model', '')).startswith('inception/mercury-decide'):
        raise ValueError('Unexpected decision model identity')
    answers = response.get('answers', {})
    for key, question in questions.items():
        answer = answers.get(key, {})
        if answer.get('type') != question['type']:
            raise ValueError('Missing or mismatched decision answer: ' + key)
        if question['type'] == 'noul':
            probability(answer.get('noul'))
        else:
            expected = set(str(i) for i in range(len(question['criteria']))) if question['type'] == 'score' else set(question['criteria'])
            distribution = answer.get('probabilities', {})
            if set(distribution) != expected or abs(sum(probability(v) for v in distribution.values()) - 1) > 0.02:
                raise ValueError('Invalid decision distribution: ' + key)
            probability(answer.get('confidence'))
            if question['type'] == 'score':
                score = answer.get('score')
                expectation = sum(int(k) * v for k, v in distribution.items())
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or abs(score - expectation) > 0.03:
                    raise ValueError('Invalid ordinal score')
            elif answer.get('choice') not in expected:
                raise ValueError('Invalid choice')
    return response


def decide(state, questions, api_key, observer):
    payload = {'model': MODEL, 'state': state, 'questions': questions}
    record = {'endpoint': ENDPOINT, 'request': payload, 'status': 'reserved'}
    token = observer('reserve', record)
    request = Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json',
        'X-Title': 'ForecastAgent isolated analysis'}, method='POST')
    try:
        with urlopen(request, timeout=120) as response:
            record.update(http_status=response.status, response_body=response.read().decode())
        record['response'] = json.loads(record['response_body'])
        validate(record['response'], questions)
        record['status'] = 'received'
    except HTTPError as exc:
        record.update(status='http_error', http_status=exc.code, response_body=exc.read().decode(errors='replace'))
        raise RuntimeError('Decision endpoint HTTP ' + str(exc.code)) from exc
    except Exception as exc:
        record.update(status='invalid_or_transport_error', error=str(exc))
        raise
    finally:
        observer('complete', record, token)
    return record['response']
