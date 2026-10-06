"""Frozen v1.0.3 paired delivery pilot; only Mercury inference uses a provider."""

import argparse
import copy
import gzip
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

from ForecastAgent.acquisition import context_handoff as delivery
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.acquisition.pipeline import verify_baseline, reject_outcomes
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.providers import decisions
from ForecastAgent.releases.v1_0_3 import verify_release
from ForecastAgent.runtime.task_lock import task_lock

ROOT = Path(__file__).resolve().parents[2]
COHORT = ROOT / 'ForecastAgent/experiments/v103_handoff_cohort.json'
FIXTURE = ROOT / 'ForecastAgent/fixtures/v103_handoff_saved.json.gz'
PROTOCOL = ROOT / 'ForecastAgent/experiments/v103_handoff_protocol.json'
LABELS = {'regression': ROOT / 'ForecastAgent/experiments/materials_handoff_score_labels.json',
          'expansion': ROOT / 'ForecastAgent/experiments/full_chain_ten_labels.json'}
ARMS = ('release', 'context')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def freeze_protocol():
    verify_release()
    baseline = verify_baseline()
    cohort = load(COHORT)
    p = {'schema': 'v103-same-material-delivery-score-v1', 'base_release': 'v1.0.3',
         'base_commit': cohort['base_commit'], 'cohort_sha256_lf': source_sha(COHORT),
         'fixture_sha256': sha(FIXTURE), 'model': decisions.MODEL, 'endpoint': decisions.ENDPOINT,
         'registry_sha256': digest(chain.questions()), 'routing': chain.ROUTING,
         'limits': {'first_bytes': chain.FIRST_BYTES, 'second_bytes': chain.SECOND_BYTES,
                    'physical_http_per_arm': 2, 'physical_http_per_question': 4, 'campaign_http': 60},
         'fixed_release_dependencies': len(baseline['initial_frozen_file_sha256']),
         'experiment_sources_sha256_lf': {str(Path(p).relative_to(ROOT)).replace('\\', '/'): source_sha(p)
                                        for p in (Path(__file__), Path(delivery.__file__))},
         'labels_sha256_lf': {phase: source_sha(path) for phase, path in LABELS.items()},
         'primary_metric': 'Paired first-pass clipped Brier; final clipped Brier and natural log loss separately.',
         'source_policy': 'Same immutable question and original bodies in both arms. No retrieval, supplement, label, community probability, model narrative or previous prediction in inference.',
         'changed_variable': 'Original evidence packing only, with exact context groups and coordinate deduplication.',
         'fixed_analysis': 'Same v1.0.3 first instructions, Mercury registry, endpoint, routing thresholds, two-stage caps and 0.02-0.98 clipping.',
         'evaluation_warning': cohort['evaluation_warning'], 'promotion_allowed': False, 'submitted': False}
    if PROTOCOL.exists() and load(PROTOCOL) != p:
        raise ValueError('Frozen experiment protocol changed; use a new identified experiment')
    save(PROTOCOL, p)
    return p


@lru_cache(maxsize=1)
def inputs():
    verify_release()
    verify_baseline()
    p = load(PROTOCOL)
    if p['model'] != decisions.MODEL or p['endpoint'] != decisions.ENDPOINT or p['registry_sha256'] != digest(chain.questions()):
        raise ValueError('Frozen decision interface changed')
    for name, expected in p['experiment_sources_sha256_lf'].items():
        if source_sha(ROOT / name) != expected:
            raise ValueError('Frozen experimental source changed: ' + name)
    if source_sha(COHORT) != p['cohort_sha256_lf'] or sha(FIXTURE) != p['fixture_sha256']:
        raise ValueError('Frozen cohort or original material changed')
    cohort = load(COHORT)
    data = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    for case in cohort['cases']:
        qid = case['question_id']
        if digest(data['bundles'][qid]) != case['projected_bundle_sha256']:
            raise ValueError('Source projection changed')
        reject_outcomes(data['bundles'][qid]['request'])
    return p, cohort, data['bundles']


def select_first(bundle, arm):
    return delivery.baseline(bundle) if arm == 'release' else delivery.pack(bundle)


def select_second(bundle, arm, first, reasons):
    if arm == 'context':
        return delivery.extend(bundle, first, reasons)
    state, audit = chain.select(packet_for(bundle), first, reasons, chain.SECOND_BYTES)
    audit['new_coordinate_chars'] = delivery.novel_chars(first, state)
    return state, audit


def preflight(phase=None):
    p, cohort, bundles = inputs()
    rows = []
    for case in cohort['cases']:
        if phase and case['phase'] != phase:
            continue
        bundle = bundles[case['question_id']]
        baseline_hash = digest(bundle)
        arms = {}
        for arm in ARMS:
            state, audit = select_first(bundle, arm)
            second, second_audit = select_second(bundle, arm, state, list(chain.CHECKS))
            errors = audit_spans(bundle, state) + audit_spans(bundle, second)
            if errors or digest(bundle) != baseline_hash:
                raise ValueError('Offline source preservation check failed')
            arms[arm] = {'first_request_bytes': chain.request_bytes(state),
                         'second_request_bytes': chain.request_bytes(second),
                         'visible_original_chars': sum(len(s['text']) for s in state['evidence']),
                         'new_reread_coordinate_chars': delivery.novel_chars(state, second),
                         'old_visible_text_removed': audit.get('old_visible_text_removed', []),
                         'new_first_coordinate_chars': audit.get('new_coordinate_chars', 0),
                         'complete_context_admissions': len(audit.get('admissions', [])),
                         'context_omissions': len(audit.get('omissions', [])),
                         'invalid_bank_count': len(audit.get('invalid_banks', [])),
                         'first_state_sha256': digest(state), 'source_integrity_errors': errors}
        rows.append({'question_id': case['question_id'], 'phase': case['phase'], 'arms': arms})
    return {'schema': 'v103-handoff-preflight-v1', 'base_release': p['base_release'],
            'rows': rows, 'passed': True, 'model_calls': 0, 'search_calls': 0,
            'fetch_calls': 0, 'submitted': False, 'protocol_sha256_lf': source_sha(PROTOCOL)}


def sealed(folder):
    result = load(folder / 'result.json')
    seal = load(folder / 'seal.json')
    if seal['result_sha256'] != digest(result):
        raise ValueError('Sealed prediction changed')
    for name, expected in seal['files_sha256'].items():
        if sha(folder / name) != expected:
            raise ValueError('Sealed input or provider record changed')
    return result


def seal(folder, result):
    save(folder / 'result.json', result)
    files = {p.relative_to(folder).as_posix(): sha(p) for p in sorted(folder.rglob('*.json'))
             if p.name not in ('result.json', 'seal.json')}
    save(folder / 'seal.json', {'result_sha256': digest(result), 'files_sha256': files})
    return result


def run_arm(bundle, folder, arm):
    folder = Path(folder)
    first, audit = select_first(bundle, arm)
    identity = {'schema': 'v103-paired-handoff-arm-v1', 'arm': arm, 'bundle_sha256': digest(bundle),
                'protocol_sha256_lf': source_sha(PROTOCOL), 'first_state_sha256': digest(first)}
    if (folder / 'identity.json').exists() and load(folder / 'identity.json') != identity:
        raise ValueError('Existing arm identity changed; never renew its budget')
    save(folder / 'identity.json', identity)
    if (folder / 'result.json').exists():
        return sealed(folder)
    save(folder / 'first-state.json', first)
    save(folder / 'first-audit.json', audit)
    result = {'status': 'failed', 'first_probability_yes': None, 'probability_yes': None,
              'selection': None, 'error': None, 'second_error': None, 'submitted': False}
    try:
        response = chain.call(first, folder / 'first')
        first_p = decisions.probability(response['answers']['event_yes']['noul'])
        result.update(status='completed', first_probability_yes=first_p,
                      probability_yes=first_p, selection='first_read')
        reasons = chain.route(response)
        second, second_audit = select_second(bundle, arm, first, reasons)
        added = delivery.novel_chars(first, second)
        gate = {'reasons': reasons, 'new_coordinate_chars': added,
                'second_call_required': bool(reasons) and added >= chain.ROUTING['minimum_new_chars']}
        save(folder / 'routing.json', gate)
        if gate['second_call_required']:
            save(folder / 'second-state.json', second)
            save(folder / 'second-audit.json', second_audit)
            try:
                response = chain.call(second, folder / 'second')
                result.update(probability_yes=decisions.probability(response['answers']['event_yes']['noul']),
                              selection='conditional_reread')
            except Exception as exc:
                result.update(status='completed_with_reread_failure', second_error=type(exc).__name__ + ': ' + str(exc))
        result['remaining_diagnostic_gaps'] = chain.route(response)
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    return seal(folder, result)


def run_case(output, qid):
    p, cohort, bundles = inputs()
    case = next((c for c in cohort['cases'] if c['question_id'] == qid), None)
    if case is None:
        raise ValueError('Question outside frozen cohort')
    root = Path(output) / qid
    with task_lock(root):
        bundle = bundles[qid]
        rows = {arm: run_arm(bundle, root / arm, arm) for arm in case['route_order']}
        result = {'question_id': qid, 'phase': case['phase'], 'bundle_sha256': digest(bundle),
                  'arms': rows, 'submitted': False, 'search_calls': 0, 'source_fetch_calls': 0}
        save(root / 'comparison.json', result)
        return result


def metric(probability, outcome):
    value = min(.98, max(.02, decisions.probability(probability)))
    return {'clipped_probability': value, 'brier': (value-outcome)**2,
            'log_loss': -(outcome * math.log(value) + (1-outcome) * math.log(1-value)),
            'correct_at_half': (value >= .5) == bool(outcome)}


def review(output, phase):
    p, cohort, bundles = inputs()
    rows, transports = [], []
    for case in cohort['cases']:
        if case['phase'] != phase:
            continue
        qid = case['question_id']
        row = {'question_id': qid, 'question': case['question'], 'arms': {}}
        for arm in ARMS:
            folder = Path(output) / qid / arm
            if not (folder / 'result.json').exists():
                row['arms'][arm] = {'status': 'missing', 'first_probability_yes': None, 'probability_yes': None}
                continue
            result = sealed(folder)
            state, audit = select_first(bundles[qid], arm)
            if digest(load(folder / 'first-state.json')) != digest(state):
                raise ValueError('Provider input differs from frozen reproduction')
            identity = load(folder / 'identity.json')
            if (identity['bundle_sha256'] != digest(bundles[qid]) or identity['arm'] != arm
                    or identity['protocol_sha256_lf'] != source_sha(PROTOCOL)):
                raise ValueError('Sealed scoring identity changed')
            if result.get('first_probability_yes') is not None:
                first_response = decisions.validate(load(folder / 'first/response.json'), chain.questions())
                if result['first_probability_yes'] != first_response['answers']['event_yes']['noul']:
                    raise ValueError('First score differs from the original provider response')
                reasons = chain.route(first_response)
                second, _ = select_second(bundles[qid], arm, state, reasons)
                added = delivery.novel_chars(state, second)
                expected_gate = {'reasons': reasons, 'new_coordinate_chars': added,
                                 'second_call_required': bool(reasons) and added >= chain.ROUTING['minimum_new_chars']}
                if load(folder / 'routing.json') != expected_gate:
                    raise ValueError('Conditional routing differs from the frozen release policy')
                selected = first_response
                if result['selection'] == 'conditional_reread':
                    if not expected_gate['second_call_required'] or load(folder / 'second-state.json') != second:
                        raise ValueError('Second request changed or was not eligible')
                    selected = decisions.validate(load(folder / 'second/response.json'), chain.questions())
                if result['probability_yes'] != selected['answers']['event_yes']['noul']:
                    raise ValueError('Final score differs from the selected original response')
            row['arms'][arm] = copy.deepcopy(result)
            row['arms'][arm]['delivery_audit'] = {
                'first_request_bytes': chain.request_bytes(state),
                'visible_original_chars': sum(len(s['text']) for s in state['evidence']),
                'new_first_coordinate_chars': audit.get('new_coordinate_chars', 0),
                'old_visible_text_removed': audit.get('old_visible_text_removed', []),
                'context_omissions': len(audit.get('omissions', []))}
            for stage in ('first', 'second'):
                state_path = folder / (stage + '-state.json')
                if state_path.exists() and audit_spans(bundles[qid], load(state_path)):
                    raise ValueError('Invalid original source coordinates')
                attempts = list((folder / stage / 'http').glob('*.json'))
                if len(attempts) > 1:
                    raise ValueError('Stage lifetime cap violated')
                for path in attempts:
                    record = load(path)
                    if (record['request']['model'] != p['model'] or record['request']['questions'] != chain.questions()
                            or record['endpoint'] != p['endpoint']):
                        raise ValueError('Frozen provider policy changed')
                    if record['request']['state'] != load(state_path):
                        raise ValueError('Transport state changed')
                    usage = record.get('response', {}).get('usage', {})
                    transports.append({'question_id': qid, 'arm': arm, 'stage': stage,
                                       'status': record['status'], 'http_status': record.get('http_status'),
                                       'tokens': usage.get('total_tokens'), 'usage': usage,
                                       'resolved_model': record.get('response', {}).get('model')})
        rows.append(row)
    # Labels are opened only after all available predictions and transports pass.
    label_path = LABELS[phase]
    if source_sha(label_path) != p['labels_sha256_lf'][phase]:
        raise ValueError('Frozen labels changed')
    labels = load(label_path)['records']
    for row in rows:
        outcome = labels[row['question_id']]['resolution']
        row['resolution'] = outcome
        for result in row['arms'].values():
            for stage, key in [('first', 'first_probability_yes'), ('final', 'probability_yes')]:
                if result.get(key) is not None:
                    result[stage + '_metrics'] = metric(result[key], outcome)
    metrics = {}
    for stage in ('first', 'final'):
        matched = [r for r in rows if all(stage + '_metrics' in r['arms'][a] for a in ARMS)]
        metrics[stage] = {'requested_pairs': len(rows), 'scored_pairs': len(matched), 'arms': {}}
        for arm in ARMS:
            scores = [r['arms'][arm][stage + '_metrics'] for r in matched]
            metrics[stage]['arms'][arm] = {k: sum(s[k] for s in scores) / len(scores) if scores else None
                                           for k in ('brier', 'log_loss', 'correct_at_half')}
    report = {'schema': 'v103-same-material-paired-review-v1', 'phase': phase, 'rows': rows, 'metrics': metrics,
              'actual_mercury_http_attempts': len(transports), 'known_reported_tokens': sum(t['tokens'] or 0 for t in transports),
              'unknown_usage_attempts': sum(t['tokens'] is None for t in transports), 'transports': transports,
              'complete': all(r['arms'][a].get('probability_yes') is not None for r in rows for a in ARMS),
              'search_calls': 0, 'source_fetch_calls': 0, 'supplement_calls': 0,
              'protocol_sha256_lf': source_sha(PROTOCOL), 'submitted': False, 'promotion_allowed': False,
              'evaluation_warning': p['evaluation_warning']}
    save(Path(output) / ('review-' + phase + '.json'), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-protocol', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--phase', choices=('regression', 'expansion'))
    parser.add_argument('--question-id')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--review', action='store_true')
    args = parser.parse_args()
    if args.freeze_protocol:
        print(json.dumps(freeze_protocol()))
    elif args.preflight:
        result = preflight(args.phase)
        if args.output:
            save(args.output, result)
        print(json.dumps({k: v for k, v in result.items() if k != 'rows'}))
    elif args.review:
        print(json.dumps({k: v for k, v in review(args.output, args.phase).items() if k not in ('rows', 'transports')}))
    else:
        result = run_case(args.output, args.question_id)
        print(json.dumps(result))
        if any(row['probability_yes'] is None for row in result['arms'].values()):
            raise SystemExit(2)


if __name__ == '__main__':
    main()
