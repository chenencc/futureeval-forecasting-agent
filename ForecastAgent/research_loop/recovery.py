"""Offline receipt-bound forecast recovery without modifying a prior identity."""
from pathlib import Path

from ForecastAgent.analysis import mercury_evidence_chain as chain, mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.providers.decisions import validate
from ForecastAgent.research_loop.decision import prepare
from ForecastAgent.research_loop.distribution import forecast
from ForecastAgent.research_loop.live_trial import sha


def replay(bundle, decision_directory, output, arm):
    """Require exact packet, registry and matching HTTP response; never calls a provider."""
    if arm not in {'baseline', 'enriched'}:
        raise ValueError('Unknown decision arm')
    directory, output = Path(decision_directory), Path(output)
    prepared = prepare(bundle)
    request_path = directory/arm/'request.json'
    response_path = directory/arm/'response.json'
    request, response = load(request_path), load(response_path)
    if request['state'] != prepared[arm] or request['questions'] != prepared['questions']:
        raise ValueError('Cached response packet or registry differs from the exact current decision input')
    validate(response, prepared['questions'])
    matched = []
    for path in sorted((directory/arm/'http').glob('*.json')):
        receipt = load(path)
        if (receipt.get('status') == 'received' and receipt.get('request') == request and
                receipt.get('response') == response):
            matched.append({'path': str(path), 'sha256': sha(path)})
    if len(matched) != 1:
        raise ValueError('Exactly one matching received HTTP receipt is required')
    question = bundle['request']; kind = question['question_type']
    if kind == 'binary':
        raw = {'probability_yes': response['answers']['event_yes']['noul']}
        arithmetic = None
    else:
        converted = forecast(response, prepared['spec'])
        raw = {'probability_yes_per_category': converted['probabilities']} if kind == 'multiple_choice' else {'continuous_cdf': converted['raw_cdf']}
        arithmetic = converted['arithmetic_audit']
    candidate = payload(question, raw)
    old_identity = load(directory/'identity.json')
    result = {'status': 'cached_recovered', 'arm': arm, 'payload': candidate,
        'new_http_attempts': 0, 'packet_and_registry_identical': True,
        'source_bundle_identity_matches': old_identity['source_bundle_sha256'] == digest(bundle),
        'prior_identity_sha256': sha(directory/'identity.json'),
        'request_sha256': sha(request_path), 'response_sha256': sha(response_path),
        'matching_received_receipts': matched, 'distribution_arithmetic': arithmetic,
        'remaining_diagnostics': (chain.route if kind == 'binary' else typed.route)(response),
        'old_identity_modified': False, 'old_forecast_modified': False, 'submitted': False}
    path = output/(arm+'-cached-recovery.json')
    if path.exists() and load(path) != result:
        raise ValueError('Frozen cached recovery input changed')
    if not path.exists():
        save(path, result)
    return result
