"""Frozen twenty-case typed evidence-delivery comparison; no retrieval or submission."""
import argparse
import copy
import gzip
import json
from functools import lru_cache
from pathlib import Path

from ForecastAgent.acquisition import typed_context_handoff as delivery
from ForecastAgent.acquisition import nonbinary_handoff_inputs as shared
from ForecastAgent.acquisition import v103_handoff_trial as durable
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.acquisition.pipeline import verify_baseline, reject_outcomes
from ForecastAgent.analysis import mercury_evidence_chain as chain, mercury_nonbinary_trial as typed
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.providers import decisions
from ForecastAgent.releases.v1_0_3 import verify_release
from ForecastAgent.runtime.task_lock import task_lock

ROOT = Path(__file__).resolve().parents[2]
COHORT = ROOT / 'ForecastAgent/experiments/v103_nonbinary_handoff_cohort.json'
FIXTURE = ROOT / 'ForecastAgent/fixtures/v103_nonbinary_handoff_saved.json.gz'
PROTOCOL = ROOT / 'ForecastAgent/experiments/v103_nonbinary_handoff_protocol.json'
LABELS = ROOT / 'ForecastAgent/experiments/v103_nonbinary_handoff_labels.json'
ARMS = ('release', 'context')
sha, source_sha, sealed, seal = durable.sha, durable.source_sha, durable.sealed, durable.seal


def freeze_protocol():
    verify_release()
    baseline = verify_baseline()
    cohort = load(COHORT)
    sources = (Path(__file__), Path(delivery.__file__), Path(shared.__file__), Path(durable.__file__))
    protocol = {'schema': 'v103-typed-paired-delivery-v1', 'base_release': 'v1.0.3',
        'base_commit': cohort['base_commit'], 'cohort_sha256_lf': source_sha(COHORT),
        'fixture_sha256': sha(FIXTURE), 'labels_sha256_lf': source_sha(LABELS),
        'model': decisions.MODEL, 'endpoint': decisions.ENDPOINT, 'routing': chain.ROUTING,
        'limits': {'first_bytes': chain.FIRST_BYTES, 'second_bytes': chain.SECOND_BYTES,
                   'physical_http_per_arm': 2, 'physical_http_per_question': 4, 'campaign_http': 80},
        'experiment_sources_sha256_lf': {p.relative_to(ROOT).as_posix(): source_sha(p) for p in sources},
        'fixed_release_dependencies': len(baseline['initial_frozen_file_sha256']),
        'changed_variable': 'Evidence packing only. Explicit registry adapter reproduces the frozen binary packing algorithm.',
        'fixed_analysis': 'Release first instructions, typed distribution registry, scale, clipping, route and coordinate novelty gate, same in both arms.',
        'metrics': 'Multiclass Brier and log loss; bounded normalized CRPS by range type. Research dates separate. Not official Metaculus scores.',
        'evaluation_warning': cohort['evaluation_warning'], 'promotion_allowed': False, 'submitted': False}
    if PROTOCOL.exists() and load(PROTOCOL) != protocol:
        raise ValueError('Frozen protocol changed; use a new experiment identity')
    save(PROTOCOL, protocol)
    return protocol


@lru_cache(maxsize=1)
def inputs():
    verify_release()
    verify_baseline()
    p = load(PROTOCOL)
    if p['model'] != decisions.MODEL or p['endpoint'] != decisions.ENDPOINT or p['routing'] != chain.ROUTING:
        raise ValueError('Frozen provider or routing policy changed')
    for name, expected in p['experiment_sources_sha256_lf'].items():
        if source_sha(ROOT / name) != expected:
            raise ValueError('Frozen source changed: ' + name)
    if source_sha(COHORT) != p['cohort_sha256_lf'] or sha(FIXTURE) != p['fixture_sha256']:
        raise ValueError('Frozen cohort or source material changed')
    cohort = load(COHORT)
    data = json.loads(gzip.decompress(FIXTURE.read_bytes()))['bundles']
    if len(cohort['cases']) != 20 or set(data) != {c['question_id'] for c in cohort['cases']}:
        raise ValueError('Exactly the frozen twenty questions are required')
    for case in cohort['cases']:
        bundle = data[case['question_id']]
        reject_outcomes(bundle['request'])
        spec = shared.spec(bundle['request'], case['metadata'])
        if digest(bundle) != case['projected_bundle_sha256'] or digest(spec) != case['distribution_spec_sha256'] or digest(typed.questions(spec)) != case['registry_sha256']:
            raise ValueError('Frozen question material or distribution registry changed')
    return p, cohort, data


def select_first(bundle, arm, registry):
    if arm not in ARMS:
        raise ValueError('Unknown evidence delivery arm')
    return delivery.baseline(bundle, registry) if arm == 'release' else delivery.pack(bundle, registry)


def select_second(bundle, arm, first, reasons, registry):
    if arm == 'context':
        return delivery.extend(bundle, first, reasons, registry)
    state, audit = chain.select(packet_for(bundle), first, reasons, chain.SECOND_BYTES, registry)
    audit['new_coordinate_chars'] = delivery.novel_chars(first, state)
    return state, audit


def identity(bundle, first, spec, registry, arm):
    return {'schema': 'v103-typed-paired-arm-v1', 'arm': arm, 'bundle_sha256': digest(bundle),
            'protocol_sha256_lf': source_sha(PROTOCOL), 'first_state_sha256': digest(first),
            'spec_sha256': digest(spec), 'registry_sha256': digest(registry)}


def gate_for(bundle, arm, first, response, registry):
    reasons = typed.route(response)
    second, audit = select_second(bundle, arm, first, reasons, registry)
    added = delivery.novel_chars(first, second)
    return second, audit, {'reasons': reasons, 'new_coordinate_chars': added,
        'second_call_required': bool(reasons) and added >= chain.ROUTING['minimum_new_chars']}


def preflight():
    p, cohort, bundles = inputs()
    rows = []
    for case in cohort['cases']:
        bundle = bundles[case['question_id']]
        before = digest(bundle)
        spec = shared.spec(bundle['request'], case['metadata'])
        registry = typed.questions(spec)
        uniform = {'answers': {'event_outcome': {'probabilities': {k: 1/len(spec['criteria']) for k in spec['criteria']}}}}
        shape = typed.forecast(uniform, spec)
        arms = {}
        for arm in ARMS:
            state, audit = select_first(bundle, arm, registry)
            second, _ = select_second(bundle, arm, state, list(typed.CHECKS), registry)
            errors = audit_spans(bundle, state) + audit_spans(bundle, second)
            if errors or before != digest(bundle) or audit.get('old_visible_text_removed'):
                raise ValueError('Original source preservation failed: ' + case['question_id'])
            arms[arm] = {'first_request_bytes': chain.request_bytes(state, registry),
                'second_request_bytes': chain.request_bytes(second, registry),
                'visible_original_chars': sum(len(s['text']) for s in state['evidence']),
                'new_first_coordinate_chars': audit.get('new_coordinate_chars', 0),
                'new_reread_coordinate_chars': delivery.novel_chars(state, second),
                'old_visible_text_removed': audit.get('old_visible_text_removed', []),
                'first_state_sha256': digest(state), 'source_integrity_errors': errors}
            if arms[arm]['first_request_bytes'] > p['limits']['first_bytes'] or arms[arm]['second_request_bytes'] > p['limits']['second_bytes']:
                raise ValueError('Registry-aware request byte cap exceeded')
        rows.append({'question_id': case['question_id'], 'type': case['type'], 'arms': arms,
                     'payload_format_valid': shape['payload_format_valid'],
                     'official_metadata_available': spec['official_metadata_available']})
    return {'schema': 'v103-typed-handoff-preflight-v1', 'rows': rows, 'passed': True,
            'model_calls': 0, 'search_calls': 0, 'fetch_calls': 0, 'submitted': False,
            'protocol_sha256_lf': source_sha(PROTOCOL)}


def run_arm(bundle, folder, arm, spec):
    folder = Path(folder)
    registry = typed.questions(spec)
    first, audit = select_first(bundle, arm, registry)
    expected = identity(bundle, first, spec, registry, arm)
    if (folder / 'identity.json').exists() and load(folder / 'identity.json') != expected:
        raise ValueError('Existing arm identity changed; never renew its budget')
    save(folder / 'identity.json', expected)
    if (folder / 'result.json').exists():
        return sealed(folder)
    save(folder / 'first-state.json', first)
    save(folder / 'first-audit.json', audit)
    save(folder / 'distribution-spec.json', spec)
    result = {'status': 'failed', 'first_forecast': None, 'forecast': None,
              'selection': None, 'error': None, 'second_error': None, 'submitted': False}
    try:
        response = chain.call(first, folder / 'first', registry)
        first_forecast = typed.forecast(response, spec)
        result.update(status='completed', first_forecast=first_forecast,
                      forecast=first_forecast, selection='first_read')
        second, second_audit, gate = gate_for(bundle, arm, first, response, registry)
        save(folder / 'routing.json', gate)
        if gate['second_call_required']:
            save(folder / 'second-state.json', second)
            save(folder / 'second-audit.json', second_audit)
            try:
                candidate = chain.call(second, folder / 'second', registry)
                forecast = typed.forecast(candidate, spec)
                response = candidate
                result.update(forecast=forecast, selection='conditional_reread')
            except Exception as exc:
                result.update(status='completed_with_reread_failure', second_error=type(exc).__name__ + ': ' + str(exc))
        result['remaining_diagnostic_gaps'] = typed.route(response)
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    return seal(folder, result)


def run_case(output, qid):
    _, cohort, bundles = inputs()
    case = next((c for c in cohort['cases'] if c['question_id'] == qid), None)
    if case is None:
        raise ValueError('Question outside frozen cohort')
    root = Path(output) / qid
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        bundle = bundles[qid]
        spec = shared.spec(bundle['request'], case['metadata'])
        rows = {arm: run_arm(bundle, root / arm, arm, spec) for arm in case['route_order']}
        result = {'question_id': qid, 'type': case['type'], 'bundle_sha256': digest(bundle),
                  'arms': rows, 'submitted': False, 'search_calls': 0, 'source_fetch_calls': 0}
        save(root / 'comparison.json', result)
        return result


def review(output):
    p, cohort, bundles = inputs()
    rows, transports = [], []
    for case in cohort['cases']:
        qid, bundle = case['question_id'], bundles[case['question_id']]
        spec = shared.spec(bundle['request'], case['metadata'])
        registry = typed.questions(spec)
        row = {'question_id': qid, 'type': case['type'], 'question': case['question'], 'arms': {}}
        for arm in ARMS:
            folder = Path(output) / qid / arm
            if not (folder / 'result.json').exists():
                row['arms'][arm] = {'status': 'missing', 'first_forecast': None, 'forecast': None}
                continue
            result = sealed(folder)
            first, audit = select_first(bundle, arm, registry)
            if load(folder / 'first-state.json') != first or load(folder / 'identity.json') != identity(bundle, first, spec, registry, arm) or load(folder / 'distribution-spec.json') != spec:
                raise ValueError('Sealed inputs differ from frozen reproduction')
            if result['first_forecast'] is not None:
                response = decisions.validate(load(folder / 'first/response.json'), registry)
                if result['first_forecast'] != typed.forecast(response, spec):
                    raise ValueError('First forecast does not match original response')
                second, _, gate = gate_for(bundle, arm, first, response, registry)
                if load(folder / 'routing.json') != gate:
                    raise ValueError('Rereading eligibility changed')
                if result['selection'] == 'conditional_reread':
                    if not gate['second_call_required'] or load(folder / 'second-state.json') != second:
                        raise ValueError('Second request was ineligible or changed')
                    response = decisions.validate(load(folder / 'second/response.json'), registry)
                if result['forecast'] != typed.forecast(response, spec):
                    raise ValueError('Final forecast does not match selected response')
            row['arms'][arm] = copy.deepcopy(result)
            row['arms'][arm]['delivery_audit'] = {'first_request_bytes': chain.request_bytes(first, registry),
                'visible_original_chars': sum(len(s['text']) for s in first['evidence']),
                'new_first_coordinate_chars': audit.get('new_coordinate_chars', 0),
                'old_visible_text_removed': audit.get('old_visible_text_removed', []),
                'context_omissions': len(audit.get('omissions', []))}
            for stage in ('first', 'second'):
                state_path = folder / (stage + '-state.json')
                if state_path.exists() and audit_spans(bundle, load(state_path)):
                    raise ValueError('Invalid source coordinates')
                attempts = list((folder / stage / 'http').glob('*.json'))
                if len(attempts) > 1:
                    raise ValueError('Stage lifetime cap violated')
                for path in attempts:
                    record = load(path)
                    if record['request'] != {'model': p['model'], 'state': load(state_path), 'questions': registry} or record['endpoint'] != p['endpoint']:
                        raise ValueError('Provider transport changed')
                    if stage == 'second' and not load(folder / 'routing.json')['second_call_required']:
                        raise ValueError('Ineligible second provider attempt')
                    usage = record.get('response', {}).get('usage', {})
                    tokens = usage.get('total_tokens')
                    if tokens is None and all(type(usage.get(k)) is int for k in ('input_tokens','output_tokens')):
                        tokens = usage['input_tokens'] + usage['output_tokens']
                    transports.append({'question_id': qid, 'arm': arm, 'stage': stage,
                        'status': record['status'], 'http_status': record.get('http_status'), 'tokens': tokens,
                        'usage': usage, 'resolved_model': record.get('response', {}).get('model')})
        rows.append(row)
    if len(transports) > p['limits']['campaign_http']:
        raise ValueError('Campaign lifetime cap violated')
    # Open outcome labels only after all available requests and responses pass audit.
    if source_sha(LABELS) != p['labels_sha256_lf']:
        raise ValueError('Frozen labels changed')
    labels = load(LABELS)['records']
    for row in rows:
        spec = shared.spec(bundles[row['question_id']]['request'], next(c['metadata'] for c in cohort['cases'] if c['question_id']==row['question_id']))
        label = labels[row['question_id']]
        row['label'] = label
        for result in row['arms'].values():
            for stage, key in [('first','first_forecast'),('final','forecast')]:
                if result[key] is not None:
                    result[stage+'_metrics'] = shared.score(result[key], spec, label)
    metrics = {}
    for kind in ('multiple_choice','numeric','discrete','date'):
        metrics[kind] = {}
        for stage in ('first','final'):
            requested = [r for r in rows if r['type']==kind]
            matched = [r for r in requested if all(stage+'_metrics' in r['arms'][a] for a in ARMS)]
            measures = ('multiclass_brier','log_loss','top_option_correct') if kind=='multiple_choice' else ('bounded_normalized_crps',)
            metrics[kind][stage] = {'requested_pairs': len(requested), 'scored_pairs': len(matched),
                'arms': {a: {m: sum(r['arms'][a][stage+'_metrics'][m] for r in matched)/len(matched) if matched else None for m in measures} for a in ARMS}}
    consumption = {}
    for arm in ARMS:
        records = [t for t in transports if t['arm']==arm]
        consumption[arm] = {'actual_http_attempts': len(records), 'known_tokens': sum(t['tokens'] or 0 for t in records),
                            'unknown_usage_attempts': sum(t['tokens'] is None for t in records)}
    result = {'schema': 'v103-typed-paired-review-v1', 'rows': rows, 'metrics': metrics,
        'complete': all(r['arms'][a]['forecast'] is not None for r in rows for a in ARMS),
        'by_arm_consumption': consumption, 'actual_mercury_http_attempts': len(transports), 'transports': transports,
        'known_reported_tokens': sum(t['tokens'] or 0 for t in transports),
        'unknown_usage_attempts': sum(t['tokens'] is None for t in transports),
        'protocol_sha256_lf': source_sha(PROTOCOL), 'search_calls': 0, 'source_fetch_calls': 0, 'supplement_calls': 0,
        'submitted': False, 'promotion_allowed': False, 'evaluation_warning': p['evaluation_warning']}
    save(Path(output) / 'review-nonbinary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-protocol', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--review', action='store_true')
    parser.add_argument('--question-id')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.freeze_protocol:
        result = freeze_protocol()
    elif args.preflight:
        result = preflight()
        if args.output:
            save(args.output, result)
    elif args.review:
        result = review(args.output)
    else:
        result = run_case(args.output, args.question_id)
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','transports','arms')}))
    if 'arms' in result and any(v['forecast'] is None for v in result['arms'].values()):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
