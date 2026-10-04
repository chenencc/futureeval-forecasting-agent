"""Experimental V3 requirement-scoped witnesses; never infer event outcomes."""
import copy
from ForecastAgent.supplement.material_contract import evaluate as evaluate_v2
from ForecastAgent.supplement.quote_alignment import bind

VERDICTS = ('matched', 'mismatched', 'unknown')
STAGES = ('not_applicable', 'proposed', 'applied', 'scheduled', 'completed',
          'appointed', 'approved', 'denied', 'observed', 'unknown')


def span_catalog(source):
    """Index exact saved ranges; no generated quotation or fuzzy text repair."""
    text = source['text']
    spans = []
    start = 0
    while start < len(text):
        end = min(start + 700, len(text))
        if end < len(text):
            boundary = max(text.rfind('\n', start + 350, end), text.rfind('. ', start + 350, end))
            if boundary >= 0:
                end = boundary + 1
        spans.append({'id': f'span-{len(spans) + 1:03d}', 'start': start, 'end': end,
            'capture_start': source.get('start', 0) + start,
            'capture_end': source.get('start', 0) + end, 'text': text[start:end]})
        start = end
    return spans


def resolve_spans(source, observation):
    """Reconstruct quotes from observed IDs; preserve proposed values separately."""
    indexed = {s['id']: s for s in span_catalog(source)}
    result = copy.deepcopy(observation)
    bindings = {}

    def resolve(ids):
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 3 or
                any(not isinstance(i, str) or i not in indexed for i in ids) or
                len(set(ids)) != len(ids)):
            raise ValueError('invalid_or_unknown_span_ids')
        return sorted([indexed[i] for i in ids], key=lambda s: s['start'])

    main = resolve(result.get('span_ids'))
    # V2 semantic/domain checks operate on a program-selected exact range. All
    # final binding coordinates below refer back to the original saved body.
    result['quote'] = main[0]['text']
    records = result.get('field_evidence')
    if not isinstance(records, list):
        raise ValueError('field_evidence_missing')
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('invalid_field_witness')
        if record.get('verdict') == 'unknown' and record.get('span_ids') == []:
            continue
        selected = resolve(record.get('span_ids'))
        record['quote'] = '\n\n'.join(s['text'] for s in selected)
        record['value'] = selected[0]['text']
        bindings[record.get('field_id')] = {'bound': True, 'spans': [
            {k: v for k, v in s.items() if k != 'id'} for s in selected],
            'binding_method': 'program_indexed_span_ids'}
    main_binding = {'bound': True, 'spans': [{k: v for k, v in s.items() if k != 'id'} for s in main],
                    'binding_method': 'program_indexed_span_ids'}
    return result, bindings, main_binding


def evaluate(contract, source, observation):
    """Close only with bound witnesses for every program-defined required field.

    The program checks identity, coordinates, stage enums and structured
    consistency. It does not prove semantic entailment or understand arbitrary
    free-text explanations. Missing V3 fields cannot be backfilled from V2
    booleans or from a human label.
    """
    bindings = {}
    main_binding = None
    base_source = source
    if isinstance(observation, dict) and 'span_ids' in observation:
        try:
            observation, bindings, main_binding = resolve_spans(source, observation)
            first = main_binding['spans'][0]
            base_source = {**source, 'text': first['text'], 'start': first['capture_start']}
        except ValueError as exc:
            return {'status': 'uncertain', 'issues': [str(exc)], 'field_bindings': [],
                    'eligible_for_need_closure': False, 'truth_verified': False,
                    'closure_scope': contract.get('closure_scope', 'target_material')}
    base = evaluate_v2(contract, base_source, observation)
    if main_binding:
        base['quote_binding'] = main_binding
    base['field_bindings'] = []
    if 'publisher_origin_mismatch' in base['issues']:
        return {**base, 'status': 'mismatched', 'eligible_for_need_closure': False}
    unresolved_role = base['status'] == 'uncertain' and base['issues'] == ['document_role_unresolved']
    if observation is None or (base['status'] != 'matched' and not unresolved_role):
        return base

    problems = list(base['issues'])
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
        alignment = bindings.get(key) or bind(source, record.get('quote'))
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
