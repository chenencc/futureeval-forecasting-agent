"""Experimental V3 requirement-scoped witnesses; never infer event outcomes."""
from ForecastAgent.supplement.material_contract import evaluate as evaluate_v2
from ForecastAgent.supplement.quote_alignment import bind

VERDICTS = ('matched', 'mismatched', 'unknown')
STAGES = ('not_applicable', 'proposed', 'applied', 'scheduled', 'completed',
          'appointed', 'approved', 'denied', 'observed', 'unknown')


def evaluate(contract, source, observation):
    """Close only with bound witnesses for every program-defined required field.

    The program checks identity, coordinates, stage enums and structured
    consistency. It does not prove semantic entailment or understand arbitrary
    free-text explanations. Missing V3 fields cannot be backfilled from V2
    booleans or from a human label.
    """
    base = evaluate_v2(contract, source, observation)
    base['field_bindings'] = []
    if observation is None or base['status'] != 'matched':
        return base

    problems = []
    mismatches = []
    requirements = contract.get('required_fields')
    if not isinstance(requirements, list) or not requirements:
        return {**base, 'status': 'uncertain', 'eligible_for_need_closure': False,
                'issues': ['required_fields_contract_missing']}
    required_ids = [r.get('id') for r in requirements if isinstance(r, dict)]
    if (len(required_ids) != len(requirements) or
            any(not isinstance(i, str) or not i for i in required_ids) or
            len(set(required_ids)) != len(required_ids)):
        return {**base, 'status': 'uncertain', 'eligible_for_need_closure': False,
                'issues': ['invalid_required_fields_contract']}
    axes = {r.get('axis') for r in requirements}
    if (not set(contract['required_axes']).issubset(axes) or
            not axes.issubset(set(contract['required_axes']))):
        return {**base, 'status': 'uncertain', 'eligible_for_need_closure': False,
                'issues': ['required_field_axis_coverage_invalid']}
    witnesses = observation.get('field_evidence')
    if not isinstance(witnesses, list):
        witnesses = []
        problems.append('field_evidence_missing')
    records = {}
    for record in witnesses:
        if not isinstance(record, dict) or record.get('field_id') not in required_ids:
            problems.append('unknown_or_invalid_field_witness')
            continue
        key = record['field_id']
        if key in records:
            problems.append('duplicate_field_witness:' + key)
        else:
            records[key] = record

    for required in requirements:
        key = required['id']
        record = records.get(key)
        if record is None:
            problems.append('missing_field_witness:' + key)
            continue
        verdict = record.get('verdict')
        explained = record.get('explanation_verdict')
        if (verdict not in VERDICTS or explained not in VERDICTS or
                verdict != explained):
            problems.append('field_explanation_conflict:' + key)
            continue
        if not isinstance(record.get('explanation'), str) or not record['explanation'].strip():
            problems.append('field_explanation_missing:' + key)
        stage = record.get('observed_stage')
        if stage not in STAGES:
            problems.append('invalid_observed_stage:' + key)
        allowed = required.get('allowed_stages', [])
        if allowed and stage == 'unknown':
            problems.append('field_stage_unknown:' + key)
        elif allowed and stage not in allowed:
            mismatches.append('field_stage_mismatch:' + key)
        if verdict == 'unknown':
            problems.append('field_unresolved:' + key)
            continue
        alignment = bind(source, record.get('quote'))
        base['field_bindings'].append({'field_id': key, 'verdict': verdict,
                                       'quote_binding': alignment})
        if not alignment['bound']:
            problems.append('field_quote_unbound:' + key)
        if verdict == 'matched':
            value = record.get('value')
            if not isinstance(value, str) or not value.strip():
                problems.append('field_value_missing:' + key)
            elif alignment['bound'] and not any(
                    ' '.join(value.split()) in ' '.join(span['text'].split())
                    for span in alignment['spans']):
                problems.append('field_value_not_in_quote:' + key)
            axis = required.get('axis')
            if observation.get('fit_axes', {}).get(axis) is not True:
                problems.append('field_axis_conflict:' + key)
        else:
            mismatches.append('field_requirement_mismatch:' + key)

    declared = observation.get('material_verdict')
    explained = observation.get('explanation_verdict')
    if declared not in VERDICTS or declared != explained:
        problems.append('material_explanation_conflict')
    if declared == 'mismatched':
        mismatches.append('declared_material_mismatch')
    elif declared == 'unknown':
        problems.append('declared_material_unknown')
    if declared == 'matched' and (mismatches or problems):
        problems.append('claimed_match_without_consistent_witnesses')
    status = 'mismatched' if mismatches else 'uncertain' if problems else 'matched'
    return {**base, 'status': status, 'issues': mismatches + problems,
            'eligible_for_need_closure': status == 'matched' and
            base['closure_scope'] == 'target_material'}
