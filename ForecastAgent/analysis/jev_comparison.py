"""Replay frozen decision requests through Jev without research or live submission."""
import argparse
import copy
import json
import math
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ForecastAgent.analysis.pilot import Journal
from ForecastAgent.competition.queue import digest, load, save

MODEL = 'typesafe/jev-1.13'
ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
MAX_REQUEST_BYTES = 28000


def encoded(payload):
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


def prepare(original):
    request = copy.deepcopy(original)
    request['model'] = MODEL
    audit = {'original_request_sha256': digest(original), 'modified_state': False, 'omissions': []}
    # UTF-8 byte count is a conservative token bound; reserve context headroom.
    if len(encoded(request)) > MAX_REQUEST_BYTES:
        evidence = request['state']['exact_evidence']
        cap = 650
        while len(encoded(request)) > MAX_REQUEST_BYTES and cap >= 100:
            audit['omissions'] = []
            for row, source in zip(evidence, original['state']['exact_evidence']):
                text = source['text']
                row['text'] = text[:cap]
                row['end'] = source['start'] + len(row['text'])
                if len(text) > cap:
                    audit['omissions'].append({'evidence_id': row['evidence_id'],
                        'original_end': source['end'], 'retained_end': row['end'],
                        'omitted_chars': len(text) - cap})
            cap -= 50
        audit['modified_state'] = True
    if len(encoded(request)) > MAX_REQUEST_BYTES:
        raise ValueError('Decision request exceeds conservative Jev context bound')
    audit.update(request_sha256=digest(request), request_bytes=len(encoded(request)))
    return request, audit


def validate(response, questions):
    if not str(response.get('model', '')).startswith(MODEL):
        raise ValueError('Unexpected Jev model identity')
    for key, question in questions.items():
        answer = response.get('answers', {}).get(key, {})
        if answer.get('type') != question['type']:
            raise ValueError('Missing typed answer: ' + key)
        if question['type'] == 'noul':
            value = answer.get('noul')
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('Invalid probability: ' + key)
        else:
            expected = {str(i) for i in range(len(question['criteria']))}
            probs = answer.get('probabilities', {})
            if set(probs) != expected or any(type(v) not in (int, float) or not math.isfinite(v)
                    or not 0 <= v <= 1 for v in probs.values()) or abs(sum(probs.values()) - 1) > .02:
                raise ValueError('Invalid score distribution: ' + key)
    return response


def metrics(rows):
    result = {'count': len(rows), 'models': {}}
    for route in ('reasoning', 'mercury', 'jev', 'reasoning_mercury_mean', 'reasoning_jev_mean', 'three_way_mean'):
        if not rows:
            continue
        ps = [row[route + '_p'] for row in rows]
        ys = [row['resolution'] for row in rows]
        result['models'][route] = {
            'brier': sum((p-y)**2 for p, y in zip(ps, ys))/len(rows),
            'log_loss': -sum(math.log(max(1e-6, min(1-1e-6, p if y else 1-p)))
                for p, y in zip(ps, ys))/len(rows),
            'accuracy_ties_as_yes': sum((p >= .5) == bool(y) for p, y in zip(ps, ys))/len(rows),
            'ties': sum(p == .5 for p in ps),
        }
    return result


def run(inputs, output):
    cohort = load(Path(__file__).with_name('jev_v8_cohort.json'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    frozen_path = output/'cohort.json'
    if frozen_path.exists() and load(frozen_path) != cohort:
        raise ValueError('Frozen evaluation cohort changed')
    save(frozen_path, cohort)
    results, failures, audits = [], [], []
    for row in cohort['rows']:
        ident, run_id = row['id'], row['run_id']
        folder = output/'tasks'/ident
        try:
            source = Path(inputs)/run_id/'tasks'/ident/'mercury-http'/'001.json'
            record = load(source)
            if record['status'] != 'received':
                raise ValueError('Baseline decision response unavailable')
            if abs(record['response']['answers']['event_yes']['noul'] - row['mercury_p']) > 1e-10:
                raise ValueError('Baseline probability mismatch')
            request, audit = prepare(record['request'])
            audit.update(id=ident, baseline_run_id=run_id, original_transport_sha256=digest(record))
            audits.append(audit)
            request_path = folder/'request.json'
            if request_path.exists() and load(request_path) != request:
                raise ValueError('Frozen Jev request changed')
            save(request_path, request)
            save(folder/'input-audit.json', audit)
            response_path = folder/'response.json'
            if response_path.exists():
                response = validate(load(response_path), request['questions'])
            else:
                journal = Journal(folder/'jev-http', 1)
                prior = list((folder/'jev-http').glob('*.json'))
                received = [load(p) for p in prior if load(p).get('status') == 'received']
                if received:
                    response = validate(received[0]['response'], request['questions'])
                else:
                    transport = {'endpoint': ENDPOINT, 'request': request, 'status': 'reserved'}
                    token = journal('reserve', transport)
                    try:
                        req = Request(ENDPOINT, data=encoded(request), method='POST', headers={
                            'Authorization': 'Bearer '+os.environ['OPENROUTER_API_KEY'],
                            'Content-Type': 'application/json', 'X-Title': 'ForecastAgent historical Jev comparison'})
                        with urlopen(req, timeout=90) as reply:
                            transport.update(http_status=reply.status,
                                response_body=reply.read().decode('utf-8'))
                        response = validate(json.loads(transport['response_body']), request['questions'])
                        transport.update(response=response, status='received')
                    except HTTPError as exc:
                        transport.update(status='http_error', http_status=exc.code,
                            response_body=exc.read().decode('utf-8', errors='replace'))
                        raise RuntimeError('Jev HTTP '+str(exc.code)) from exc
                    except Exception as exc:
                        transport.update(status='invalid_or_transport_error', error=str(exc))
                        raise
                    finally:
                        journal('complete', transport, token)
                save(response_path, response)
            p = response['answers']['event_yes']['noul']
            result = {**row, 'jev_p': p, 'reasoning_mercury_mean_p': (row['reasoning_p']+row['mercury_p'])/2,
                'reasoning_jev_mean_p': (row['reasoning_p']+p)/2,
                'three_way_mean_p': (row['reasoning_p']+row['mercury_p']+p)/3,
                'modified_state': audit['modified_state'], 'usage': response.get('usage'),
                'served_model': response['model']}
            result['reasoning_p'] = row['reasoning_p']
            save(folder/'result.json', result)
            results.append(result)
            print(json.dumps({'id': ident, 'status': 'completed', 'modified_state': audit['modified_state']}), flush=True)
        except Exception as exc:
            failure = {'id': ident, 'error': type(exc).__name__+': '+str(exc)}
            failures.append(failure)
            save(folder/'failure.json', failure)
            print(json.dumps(failure), flush=True)
        save(output/'progress.json', {'completed': len(results), 'failed': len(failures), 'requested': len(cohort['rows'])})
    transports = [load(p) for p in output.glob('tasks/*/jev-http/*.json')]
    usage = [r.get('response', {}).get('usage') or {} for r in transports]
    report = {'schema': 'jev-v8-comparison-v1', 'model': MODEL, 'requested': len(cohort['rows']),
        'completed': len(results), 'failures': failures, 'rows': results, 'input_audits': audits,
        'all_completed': metrics(results), 'identical_state_only': metrics([r for r in results if not r['modified_state']]),
        'compressed_state_only': metrics([r for r in results if r['modified_state']]),
        'actual_http_attempts': len(transports), 'known_input_tokens': sum(u.get('input_tokens', 0) for u in usage),
        'known_cost_usd': sum(u.get('cost', 0) for u in usage),
        'unknown_usage_attempts': sum('input_tokens' not in u for u in usage),
        'no_retrieval_or_reasoning_calls': True, 'no_forecasts_submitted': True,
        'warnings': cohort['evaluation_warning']+['One lifetime HTTP attempt per question; failures are never imputed.',
            'Compressed inputs are reported separately. Raw model probabilities are scored without clipping.']}
    save(output/'report.json', report)
    print(json.dumps({'completed': len(results), 'failed': len(failures), 'known_cost_usd': report['known_cost_usd']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run(args.inputs, args.output)
