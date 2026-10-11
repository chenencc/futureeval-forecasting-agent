"""Read sealed original/map pairs without model calls or modifying their archive."""
import argparse
import hashlib
from datetime import datetime
from pathlib import Path

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.providers.decisions import validate
from ForecastAgent.research_loop.conditional import evaluate as decode
from ForecastAgent.research_loop.evaluate_trial import score
from ForecastAgent.research_loop.labels import validate as validate_label
from ForecastAgent.runtime.records import load, save


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return parsed


def temporal(forecast_time, question, binding):
    """An archived outcome is not proof that a forecast preceded public evidence."""
    result = {'status': 'first_public_outcome_unknown', 'prospective_eligible': False,
              'official_deadline_eligible': False, 'source_truth_verified': False}
    try:
        forecast = instant(forecast_time)
        deadline = question.get('scheduled_close_time') or question.get('close_time')
        if deadline:
            result['official_deadline_eligible'] = forecast < instant(deadline)
        provenance = binding.get('provenance', {}) if isinstance(binding, dict) else {}
        first = provenance.get('first_public_outcome_at')
        if first is not None:
            before = forecast < instant(first)
            reviewed = provenance.get('first_public_outcome_reviewed') is True
            result.update(first_public_outcome_at=first,
                          status='after_public_outcome' if not before else
                          'before_reviewed_public_outcome' if reviewed else 'public_outcome_time_unreviewed',
                          prospective_eligible=before and reviewed)
        result['official_deadline_known'] = bool(deadline)
    except (ValueError, TypeError, AttributeError) as exc:
        result.update(status='invalid_time_contract', error=str(exc))
    return result


def arm(folder, prepared, model):
    """Require a separate received HTTP record and decode its original reply."""
    folder = Path(folder)
    receipts = list((folder/'decision/http').glob('*.json'))
    if len(receipts) != 1:
        raise ValueError('Independent arm requires exactly one preserved HTTP record')
    receipt = load(receipts[0])
    expected = {'model': model, 'state': prepared['state'], 'questions': prepared['questions']}
    if receipt['status'] != 'received' or receipt['request'] != expected:
        raise ValueError('Received request does not match frozen prepared input')
    validate(receipt['response'], prepared['questions'])
    saved = load(folder/'result.json')
    replay = decode(prepared, receipt['response'])
    if saved['status'] != 'completed' or saved['payload'] != replay['payload']:
        raise ValueError('Saved forecast does not match independently decoded receipt')
    return {'payload': saved['payload'], 'receipt_sha256': sha(receipts[0]),
            'result_sha256': sha(folder/'result.json'),
            'request_sha256': digest(receipt['request'])}


def evaluate(root, bindings=None):
    root = Path(root); bindings = bindings or {}
    registration = load(root/'registration.json')
    if registration.get('schema') != 'unresolved_original_map_three_pair_v1':
        raise ValueError('Unsupported frozen trial registration')
    if load(root/'closure.json').get('restarts_allowed') is not False:
        raise ValueError('Only a closed, immutable trial can be evaluated')
    rows = []
    for case in registration['rows']:
        ident = case['id']; folder = root/ident; binding = bindings.get(ident)
        row = {'id': ident, 'kind': case['type'], 'evaluation_status': 'pending_outcome'}
        try:
            path = folder/'common-originals.json'; common = load(path)
            if sha(path) != case['new_common_sha256']:
                raise ValueError('Frozen common originals changed')
            question = common['question']
            if str(question['id']) != ident or question['question_type'] != case['type']:
                raise ValueError('Frozen question identity changed')
            if question['as_of_utc'] != registration['forecast_time']:
                raise ValueError('Forecast clock changed')
            a = load(folder/'original-prepared.json'); b = load(folder/'mapped-prepared.json')
            if a['questions'] != b['questions'] or any(
                    a['state'].get(k) != v or b['state'].get(k) != v for k, v in common.items()):
                raise ValueError('Arms do not share exact originals and target registry')
            if a['state'].get('research_map'):
                raise ValueError('Original-only control contains a map')
            row['original'] = arm(folder/'original', a, registration['mercury_model'])
            status_path = folder/'preserved-inputs/official-authenticated-status.json'
            if sha(status_path) != case['official_status_sha256']:
                raise ValueError('Archived official status changed')
            status = load(status_path)
            if str(status['id']) != ident or str(status['post_id']) != str(question['post_id']):
                raise ValueError('Archived official status belongs to another question')
            clock_question = dict(question)
            clock_question['close_time'] = status.get('actual_close_time') or status.get('scheduled_close_time')
            row['temporal'] = temporal(registration['forecast_time'], clock_question, binding)
            row['temporal']['official_status_sha256'] = sha(status_path)
            row['temporal']['registered_official_window'] = case['official_prediction_window']
            if case['official_prediction_window'] == 'closed':
                row['temporal']['official_deadline_eligible'] = False
            row['label_binding'] = validate_label(question, binding)
            independent = bool(b['state'].get('research_map', {}).get('factual_grounding_present'))
            row['independent_pair'] = independent
            if not independent:
                if list((folder/'mapped/decision/http').glob('*.json')):
                    raise ValueError('Gap-only arm unexpectedly has a scoring request')
                row['evaluation_status'] = 'not_independent'
            else:
                row['mapped'] = arm(folder/'mapped', b, registration['mercury_model'])
                if binding is not None:
                    if not row['label_binding']['eligible']:
                        row['evaluation_status'] = 'quarantined_label'
                    else:
                        row['metrics'] = {name: score(row[name]['payload'], question, binding['label'])
                                          for name in ('original', 'mapped')}
                        row['difference_B_minus_A'] = {k: row['metrics']['mapped'][k]-v
                                                       for k, v in row['metrics']['original'].items()}
                        row['evaluation_status'] = 'evaluated_diagnostic'
        except (ValueError, KeyError, TypeError, OSError) as exc:
            row.update(evaluation_status='quarantined_pair', error=str(exc))
            row.pop('metrics', None); row.pop('difference_B_minus_A', None)
        rows.append(row)
    groups = {}
    for kind in {r['kind'] for r in rows}:
        matched = [r for r in rows if r['kind'] == kind and r['evaluation_status'] == 'evaluated_diagnostic']
        prospective = [r for r in matched if r['temporal']['prospective_eligible']]
        groups[kind] = {'diagnostic_n': len(matched), 'prospective_n': len(prospective),
                        'prospective_metrics': {}}
        for key in (prospective[0]['metrics']['original'] if prospective else {}):
            av = sum(r['metrics']['original'][key] for r in prospective)/len(prospective)
            bv = sum(r['metrics']['mapped'][key] for r in prospective)/len(prospective)
            groups[kind]['prospective_metrics'][key] = {'A': av, 'B': bv, 'B_minus_A': bv-av}
    return {'schema': 'sealed-shadow-evaluation-v1', 'registration_sha256': sha(root/'registration.json'),
            'closure_sha256': sha(root/'closure.json'), 'labels_sha256': digest(bindings),
            'rows': rows, 'by_type': groups, 'new_http': 0, 'submitted': False,
            'full_production_release_comparison': False,
            'limitation': 'Identity-bound outcome diagnostics and reviewed public-event timing do not certify source truth, model-memory cleanliness or performance against the entire production release.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--labels', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.root.resolve()):
        raise ValueError('Evaluation output must be outside the sealed trial')
    save(args.output, evaluate(args.root, load(args.labels) if args.labels else {}))


if __name__ == '__main__':
    main()
