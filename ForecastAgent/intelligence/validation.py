"""Freeze paired inputs, seal results, and separate diagnostics from forecasts.

No retrieval, inference, submission or automatic label retrieval occurs here.
Hash checks establish local integrity, not independently certified timestamps or
semantic truth. Prospective qualification requires explicit outcome-time evidence.
"""
import argparse
import copy
import math
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.acquisition.pipeline import reject_outcomes
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.analysis.distributions import grid, range_metadata
from ForecastAgent.research_loop.evaluate_trial import score
from ForecastAgent.research_loop.labels import contract, validate as validate_label

PROTOCOL = 'sealed-paired-validation-v1'
ARMS = ('original', 'mapped')
MODES = {'retrospective_diagnostic', 'prospective_shadow'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Explicit timezone-aware timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Explicit timezone required')
    return result.astimezone(timezone.utc)


def verified(directory):
    directory = Path(directory)
    manifest = load(directory / 'manifest.json')
    if manifest.get('schema') != PROTOCOL or manifest['manifest_sha256'] != digest(
            {k: v for k, v in manifest.items() if k != 'manifest_sha256'}):
        raise ValueError('Validation manifest checksum mismatch')
    for arm in ARMS:
        if digest(load(directory / (arm + '-input.json'))) != manifest['input_sha256'][arm]:
            raise ValueError('Sealed model input changed: ' + arm)
    return manifest


def freeze_case(question, pair, directory, *, cutoff_utc, mode, configuration,
                status_receipt=None):
    """Register inputs before inference. Retroactive registration stays diagnostic."""
    if mode not in MODES:
        raise ValueError('Explicit validation mode required')
    reject_outcomes(question)
    if set(pair) != set(ARMS):
        raise ValueError('Both predefined arms are required')
    inputs = {a: copy.deepcopy({k: pair[a][k] for k in ('state', 'questions')}) for a in ARMS}
    for value in inputs.values():
        reject_outcomes(value)
    original, mapped = (inputs[a]['state'] for a in ARMS)
    expected_question = contract(question)
    delivered_question = original.get('question', {})
    for key, value in expected_question.items():
        if value is not None and (key in delivered_question or key in {'id', 'question_type', 'question', 'resolution_criteria'}) and delivered_question.get(key) != value:
            raise ValueError('Delivered question differs from the frozen contract: ' + key)
    if 'research_map' in original or {k: v for k, v in mapped.items() if k != 'research_map'} != original:
        raise ValueError('Both arms must receive identical originals; map is the only added field')
    if inputs['original']['questions'] != inputs['mapped']['questions']:
        raise ValueError('Both arms must use identical scoring heads')
    if not isinstance(configuration, dict) or not configuration.get('decision_model'):
        raise ValueError('Freeze the model and scoring configuration before inference')
    cutoff = timestamp(cutoff_utc)
    receipt = copy.deepcopy(status_receipt)
    if mode == 'prospective_shadow':
        if not isinstance(receipt, dict) or str(receipt.get('question_id')) != str(question['id']):
            raise ValueError('Independent status receipt for this question is required')
        if receipt.get('status') not in {'open', 'closed'} or receipt.get('outcome_known') is not False:
            raise ValueError('Unresolved open/closed status and explicitly unknown outcome are required')
        if not receipt.get('source_url') or not receipt.get('source_sha256'):
            raise ValueError('Status receipt provenance required')
        if timestamp(receipt.get('observed_at_utc')) > cutoff:
            raise ValueError('Status receipt is later than the evidence cutoff')
    issues = []
    if not question.get('resolution_criteria') or question.get('official_rules_available') is False:
        issues.append({'reason': 'official_resolution_rules_unavailable'})
    for source in original.get('sources', []):
        try:
            native_time = source.get('capture_metadata', {}).get('retrieved_at_utc')
            direct_time = source.get('capture_time')
            captured = timestamp(direct_time if direct_time is not None else native_time)
            if direct_time is not None and native_time is not None and timestamp(native_time) != captured:
                issues.append({'source_id': source.get('source_id'), 'reason': 'capture_time_conflict'})
            if captured > cutoff:
                issues.append({'source_id': source.get('source_id'), 'reason': 'captured_after_cutoff'})
        except (ValueError, TypeError, AttributeError):
            issues.append({'source_id': source.get('source_id'), 'reason': 'capture_time_unknown'})
    if not original.get('sources') or not original.get('evidence'):
        issues.append({'reason': 'no_delivered_original_evidence'})
    identity = {'schema': PROTOCOL, 'question': contract(question), 'mode': mode,
                'cutoff_utc': cutoff_utc, 'configuration': copy.deepcopy(configuration),
                'status_receipt': receipt, 'input_sha256': {a: digest(inputs[a]) for a in ARMS},
                'temporal_issues': issues, 'map_delivered': bool(mapped.get('research_map'))}
    directory = Path(directory)
    if (directory / 'manifest.json').exists():
        prior = verified(directory)
        if any(prior[k] != v for k, v in identity.items()):
            raise ValueError('Frozen validation identity changed')
        return prior
    created = utc_now()
    if cutoff > timestamp(created):
        raise ValueError('Evidence cutoff cannot be in the future at registration')
    for arm in ARMS:
        save(directory / (arm + '-input.json'), inputs[arm])
    manifest = {**identity, 'created_at_utc': created,
                'model_memory_contamination_excluded': False,
                'semantic_truth_verified': False, 'submission_enabled': False}
    manifest['manifest_sha256'] = digest(manifest)
    save(directory / 'manifest.json', manifest)
    return manifest


def validate_payload(question, value):
    kind = question['question_type']
    if kind == 'binary':
        values = [value['probability_yes']]
    elif kind == 'multiple_choice':
        probs = value['probability_yes_per_category']
        if set(probs) != set(question['options']):
            raise ValueError('Exact categorical options required')
        values = list(probs.values())
        if not math.isclose(sum(values), 1.0, abs_tol=1e-7):
            raise ValueError('Categorical probabilities do not sum to one')
    elif kind in {'numeric', 'discrete', 'date'}:
        values = value['continuous_cdf']
        if len(values) != len(grid(range_metadata(question))):
            raise ValueError('CDF length does not match the frozen platform scale')
    else:
        raise ValueError('Unsupported question type')
    if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in values):
        raise ValueError('Invalid probability values')
    if kind in {'numeric', 'discrete', 'date'} and any(a > b for a, b in zip(values, values[1:])):
        raise ValueError('Non-monotone CDF')


def seal_result(directory, arm, result):
    """Seal successes and failures; no silent replacement, retry, or survivor filter."""
    if arm not in ARMS:
        raise ValueError('Unknown comparison arm')
    directory = Path(directory)
    manifest = verified(directory)
    path = directory / (arm + '-sealed.json')
    if path.exists():
        prior = load(path)
        if prior['seal_sha256'] != digest({k: v for k, v in prior.items() if k != 'seal_sha256'}):
            raise ValueError('Prediction seal checksum mismatch')
        if prior['result'] != result or prior['arm'] != arm or prior['manifest_sha256'] != manifest['manifest_sha256']:
            raise ValueError('Sealed result cannot be replaced')
        return prior
    if result.get('status') == 'completed':
        validate_payload(manifest['question'], result['payload'])
    elif result.get('status') not in {'failed', 'blocked', 'not_run'}:
        raise ValueError('Explicit terminal attempt status required')
    completed = utc_now()
    if timestamp(completed) < timestamp(manifest['created_at_utc']):
        raise ValueError('Result precedes registration')
    sealed = {'manifest_sha256': manifest['manifest_sha256'], 'arm': arm,
              'completed_at_utc': completed, 'result': copy.deepcopy(result)}
    sealed['seal_sha256'] = digest(sealed)
    save(path, sealed)
    return sealed


def evaluate_case(directory, binding):
    """Read isolated labels only after checking the sealed input and results."""
    directory = Path(directory)
    manifest = verified(directory)
    seals = {}
    for arm in ARMS:
        path = directory / (arm + '-sealed.json')
        if path.exists():
            sealed = load(path)
            if sealed['manifest_sha256'] != manifest['manifest_sha256'] or sealed['arm'] != arm or sealed['seal_sha256'] != digest(
                    {k: v for k, v in sealed.items() if k != 'seal_sha256'}):
                raise ValueError('Sealed prediction changed')
            if timestamp(sealed['completed_at_utc']) < timestamp(manifest['created_at_utc']):
                raise ValueError('Prediction predates registration')
            if sealed['result'].get('status') == 'completed':
                validate_payload(manifest['question'], sealed['result']['payload'])
            seals[arm] = sealed
    row = {'id': str(manifest['question']['id']), 'question_type': manifest['question']['question_type'],
           'mode': manifest['mode'], 'arms': {a: seals.get(a, {}).get('result', {}).get('status', 'missing') for a in ARMS},
           'map_delivered': manifest['map_delivered'], 'eligible_forecast_evaluation': False,
           'pending_resolution': binding is None, 'diagnostic_scores': {}, 'exclusion_reasons': [],
           'timestamp_certification': 'Local sealed records; independent timestamp witness not verified'}
    if not all(row['arms'][a] == 'completed' for a in ARMS):
        row['exclusion_reasons'].append('incomplete_pair')
    if not manifest['map_delivered']:
        row['exclusion_reasons'].append('original_only_fallback_not_map_experiment')
    if binding is None:
        return row
    audit = validate_label(manifest['question'], binding)
    row['label_binding'] = audit
    if not audit['eligible']:
        row['exclusion_reasons'].append('label_identity_or_provenance_not_verified')
        return row
    for arm, sealed in seals.items():
        if sealed['result']['status'] == 'completed':
            value = sealed['result']['payload']
            kind = manifest['question']['question_type']
            if kind == 'binary':
                p, truth = value['probability_yes'], binding['label']['value']
                row['diagnostic_scores'][arm] = {'brier': (p - truth) ** 2,
                    'log_loss': -math.log(max(1e-12, p if truth else 1 - p))}
            elif kind == 'multiple_choice':
                probs, truth = value['probability_yes_per_category'], binding['label']['resolution_display']
                row['diagnostic_scores'][arm] = {'multiclass_brier': sum((p - float(k == truth)) ** 2 for k, p in probs.items()),
                    'log_loss': -math.log(max(1e-12, probs[truth]))}
            else:
                row['diagnostic_scores'][arm] = score(value, manifest['question'], binding['label'])
    if manifest['mode'] != 'prospective_shadow':
        row['exclusion_reasons'].append('retrospective_or_model_memory_contamination_possible')
    row['exclusion_reasons'].extend(i['reason'] for i in manifest['temporal_issues'])
    provenance = binding['provenance']
    try:
        available = timestamp(provenance.get('information_available_at_utc'))
        if not provenance.get('information_time_source_url'):
            raise ValueError('Outcome availability provenance missing')
        if available <= timestamp(manifest['created_at_utc']) or any(
                available <= timestamp(s['completed_at_utc']) for s in seals.values()):
            row['exclusion_reasons'].append('outcome_known_before_prediction_sealed')
    except (ValueError, TypeError):
        row['exclusion_reasons'].append('first_public_outcome_time_unverified')
    row['eligible_forecast_evaluation'] = not row['exclusion_reasons']
    if row['eligible_forecast_evaluation']:
        row['forecast_scores'] = copy.deepcopy(row['diagnostic_scores'])
        row['difference_mapped_minus_original'] = {k: row['forecast_scores']['mapped'][k] - v
                                                  for k, v in row['forecast_scores']['original'].items()}
    return row


def evaluate(root, bindings):
    manifests = [verified(p.parent) for p in sorted(Path(root).glob('*/manifest.json'))]
    identifiers = [str(m['question']['id']) for m in manifests]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Duplicate questions would overweight the paired cohort')
    if len({digest(m['configuration']) for m in manifests}) > 1:
        raise ValueError('Evaluate different frozen configurations in separate cohorts')
    rows = [evaluate_case(p.parent, bindings.get(str(load(p)['question']['id'])))
            for p in sorted(Path(root).glob('*/manifest.json'))]
    groups = {}
    for kind in sorted({r['question_type'] for r in rows}):
        matched = [r for r in rows if r['question_type'] == kind and r['eligible_forecast_evaluation']]
        groups[kind] = {'eligible_paired_n': len(matched), 'metrics': {}}
        for metric in (matched[0]['difference_mapped_minus_original'] if matched else {}):
            groups[kind]['metrics'][metric] = {a: sum(r['forecast_scores'][a][metric] for r in matched) / len(matched) for a in ARMS}
            groups[kind]['metrics'][metric]['mapped_minus_original'] = sum(r['difference_mapped_minus_original'][metric] for r in matched) / len(matched)
    report = {'schema': PROTOCOL, 'registered_n': len(rows), 'rows': rows, 'by_type': groups,
              'log_loss_epsilon': 1e-12,
              'pending_resolution_n': sum(r['pending_resolution'] for r in rows),
              'incomplete_pairs_n': sum('incomplete_pair' in r['exclusion_reasons'] for r in rows),
              'historical_accuracy_is_forecast_evidence': False, 'promotion_recommended': False,
              'limitations': ['Local seals do not independently certify timestamps or source truth.',
                              'Finite-range CDF loss is diagnostic and does not distinguish out-of-range tails.',
                              'Small, selected cohorts require future chronological holdout confirmation.']}
    save(Path(root) / 'validation-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.root, load(args.labels))
    print({'registered_n': report['registered_n'], 'pending_resolution_n': report['pending_resolution_n'],
           'incomplete_pairs_n': report['incomplete_pairs_n'], 'by_type': report['by_type']})
