"""Rule-bound operators, separate parameters and durable observation objects."""
import copy
from ForecastAgent.providers import decisions
from ForecastAgent.supplement import mercury_material_v5 as v5
from ForecastAgent.supplement import rule_operator_contracts as operators

PROTOCOL = 'mercury-material-operator-ledgers-v6'
TIME_KINDS = {
    'any_time_by': ('by_boundary', 'before_boundary', 'interval'),
    'as_of': ('as_of_date', 'on_date'),
    'throughout': ('interval', 'by_boundary', 'before_boundary'),
    'event_time': ('on_date', 'before_boundary', 'by_boundary')}


def prepare_contract(bundle, plan):
    prepared = v5.prepare_contract(bundle, plan)
    cues = operators.inventory(plan)
    operators.verify(cues, bundle)
    prepared['state']['operator_inventory'] = copy.deepcopy(cues)
    prepared['state']['observation_ledgers'] = [operators.ledger(n, plan['rule_catalog']) for n in plan['needs']]
    prepared['state']['instruction'] += (
        ' Select time/polarity/effect operator references only from exact original rule spans. '
        'A lexical cue is not semantic verification. Classify original rule meaning, not merely '
        'a possibly incorrect need paraphrase. A price-greater-than predicate and an observed '
        'date require actual observations, even though their threshold/deadline are constants. '
        'Classifying a need parameter never erases its independent observation obligation.')
    for index, row in enumerate(prepared['registry']):
        need = plan['needs'][index]
        allowed = [c for c in cues if c['rule_id'] in need['rule_ids']]
        selections = {}
        for field in ('time', 'polarity', 'effect'):
            choices = {}
            if field == 'time':
                choices['UNKNOWN'] = {'type': 'mixed_or_unknown', 'operator_id': None}
                choices['NOT_APPLICABLE'] = {'type': 'not_applicable', 'operator_id': None}
                for cue in allowed:
                    for mode, kinds in TIME_KINDS.items():
                        if cue['kind'] in kinds:
                            choices[mode+'|'+cue['operator_id']] = {'type': mode, 'operator_id': cue['operator_id']}
            elif field == 'polarity':
                choices = {'POSITIVE': {'type': 'positive', 'operator_id': None},
                    'UNKNOWN': {'type': 'mixed_or_unknown', 'operator_id': None}}
                for cue in allowed:
                    if cue['kind'] == 'negation':
                        choices['negative|'+cue['operator_id']] = {'type': 'negative', 'operator_id': cue['operator_id']}
            else:
                choices = {'NONE': {'type': 'unassessed', 'operator_id': None}}
                for cue in allowed:
                    if cue['kind'] in ('active_state', 'operative_effect'):
                        choices[cue['kind']+'|'+cue['operator_id']] = {'type': cue['kind'], 'operator_id': cue['operator_id']}
            key = row['keys'].get(field, f'contract_{index}_{field}')
            row['keys'][field] = key
            criteria = {}
            for choice, selected in choices.items():
                ref = selected['operator_id']
                criteria[choice] = (f'Proposed {selected["type"]}; bind ONLY operator {ref} from operator_inventory.'
                    if ref else f'{selected["type"]}; no original operator selected, semantic scope remains unverified.')
            prepared['questions'][key] = {'type': 'choice', 'criteria': criteria,
                'instructions': f'Read needs[{index}], its original cited rules and exact operator_inventory spans. Select {field} AND its original operator jointly. Do not adopt an as-of snapshot from the paraphrased need if the original rule only supplies a by/before condition. Positive/unknown never proves a negative condition. For an operative state/effect, select the original active-state/effect clause, not historical issuance. Do not see other head answers.'}
            selections[field] = choices
        row['operator_choices'] = selections
    prepared['operator_inventory'] = cues
    prepared['plan'] = copy.deepcopy(plan)
    return prepared


def bind_contract(prepared, response):
    decisions.validate(response, prepared['questions'])
    if set(response['answers']) != set(prepared['questions']):
        raise ValueError('unexpected_or_missing_contract_ids')
    inventory = {c['operator_id']: c for c in prepared['operator_inventory']}
    needs = {n['id']: n for n in prepared['plan']['needs']}
    rows = []
    for item in prepared['registry']:
        answers = {f: copy.deepcopy(response['answers'][key]) for f, key in item['keys'].items()}
        types = {f: a['choice'] for f, a in answers.items() if f not in ('time', 'polarity', 'effect')}
        bindings = []
        for field, choices in item['operator_choices'].items():
            selected = choices[answers[field]['choice']]
            types[field] = selected['type']
            if selected['operator_id']:
                bindings.append(copy.deepcopy(inventory[selected['operator_id']]))
        risks = operators.risks(needs[item['need_id']], bindings)
        applicable = [c for c in inventory.values() if c['rule_id'] in needs[item['need_id']]['rule_ids']]
        effect_kinds = ('active_state', 'operative_effect')
        if types['kind'] in ('observation', 'mixed') and any(c['kind'] in effect_kinds for c in applicable) and not any(b['kind'] in effect_kinds for b in bindings):
            risks.append('original_operative_effect_operator_unbound')
        rows.append({'need_id': item['need_id'], 'original_condition': item['condition'],
            'types': types, 'decisions': answers, 'operator_bindings': bindings,
            'program_risks': risks,
            'semantic_equivalence_verified': False})
    return {'rows': rows, 'types_are_model_proposals': True,
        'operator_inventory': copy.deepcopy(prepared['operator_inventory'])}


def prepare(bundle, plan, contract):
    operators.verify(contract['operator_inventory'], bundle)
    prepared = v5.prepare(bundle, plan, contract)
    prepared['state']['operator_inventory'] = copy.deepcopy(contract['operator_inventory'])
    prepared['state']['observation_ledgers'] = [operators.ledger(n, plan['rule_catalog']) for n in plan['needs']]
    contract_rows = {r['need_id']: r for r in contract['rows']}
    for index, registry in enumerate(prepared['registry']):
        typed = contract_rows[registry['need_id']]
        registry['operator_bindings'] = copy.deepcopy(typed['operator_bindings'])
        registry['program_risks'] = copy.deepcopy(typed['program_risks'])
        prepared['state']['typed_contracts'][index].update(
            operator_bindings=[{'operator_id': b['operator_id']} for b in typed['operator_bindings']],
            program_risks=typed['program_risks'])
        prepared['questions'][registry['key']]['instructions'] += (
            ' The original need remains recorded even when inconsistent. An observation and its '
            'rule-defined target remain separate objects. A parameter type label cannot answer '
            'an observed-price predicate or observed time. See program_risks and operator_inventory. '
            'Original active-state/effect clauses require evidence of that effect at the original '
            'observation time, not a historical announcement or a sentence already served.')
    prepared['state']['instruction'] += (
        ' Treat the separate observation ledger as durable. Literal context never completes it. '
        'Lexical operator bindings verify exact text only; assess scope and applicable time from '
        'original rules. Conflicting paraphrases remain open, never silently repaired.')
    return prepared


def bind(prepared, response, bundle):
    result = v5.bind(prepared, response, bundle)
    operators.verify(prepared['contract']['operator_inventory'], bundle)
    registries = {r['need_id']: r for r in prepared['registry']}
    needs = {n['id']: n for n in prepared['state']['needs']}
    for row in result['rows']:
        registry = registries[row['need_id']]
        ledger = operators.ledger(needs[row['need_id']], prepared['rule_inventory'])
        ledger['observation_obligation']['evidence_status'] = row['relation']
        if row['relation'] in ('parameter_context', 'literal_identity'):
            ledger['literal_context'] = {'binding': copy.deepcopy(row['binding']), 'status': row['relation']}
            ledger['observation_obligation']['evidence_status'] = 'unassessed'
            row['candidate_status'] = 'literal_context_retained_observation_unassessed'
        elif row['relation'] == 'explicit':
            row['candidate_status'] = 'pending_original_rule_scope_review'
        row.update(ledger=ledger, operator_bindings=registry['operator_bindings'],
            program_risks=registry['program_risks'], scope_review=None)
        if registry['program_risks']:
            row['consistency_flags'].extend(registry['program_risks'])
    result['schema'] = PROTOCOL
    return result


def prepare_guard(prepared, result):
    rows = [r for r in result['rows'] if r['relation'] == 'explicit' and r.get('binding', {}).get('kind') == 'source']
    state = {k: copy.deepcopy(prepared['state'][k]) for k in ('question', 'needs', 'reading', 'rule_catalog', 'operator_inventory')}
    # Keep selected source coordinates, not a duplicate body or an asserted fact.
    state['scope_claims'] = [{k: copy.deepcopy(r[k]) for k in ('need_id', 'original_condition', 'types', 'operator_bindings', 'program_risks')}
        | {'selected_source': {k: r['binding'][k] for k in ('url', 'start', 'end')}} for r in rows]
    questions = {}
    for index, row in enumerate(rows):
        questions[f'scope_{index}'] = {'type': 'choice', 'criteria': {
            'entailed': 'Original source explicitly establishes this observation AND the relevant original rule operators, including time, negation and currently operative effect. Historical issuance is insufficient.',
            'contradicted': 'Original text explicitly contradicts the needed observation/effect at the relevant time. This is not an exhaustive world-event refutation.',
            'unestablished': 'At least one required field, time, stage, polarity or original-rule scope is unestablished or conflicting.',
            'literal_only': 'Only a literal rule constant, identity or reference date is established; the separate world observation remains unassessed.'},
            'instructions': f'Read scope_claims[{index}], ORIGINAL rules/operator spans and ORIGINAL reading, including the selected source. The earlier explicit relation and type labels are fallible. Review rule-scope entailment, not whether the source mentions the same entity/event. Distinguish historical imposition from effect still operative at the specified time, and observed values from target constants. A sentence served/expired does not establish an active prohibition. Do not infer absence from omissions or other head answers.'}
    return {'state': state, 'questions': questions, 'need_ids': [r['need_id'] for r in rows]}


def bind_guard(result, prepared, response):
    decisions.validate(response, prepared['questions'])
    if set(response['answers']) != set(prepared['questions']):
        raise ValueError('unexpected_or_missing_scope_ids')
    revised = copy.deepcopy(result)
    rows = {r['need_id']: r for r in revised['rows']}
    for index, need_id in enumerate(prepared['need_ids']):
        row = rows[need_id]
        answer = copy.deepcopy(response['answers'][f'scope_{index}'])
        row['scope_review'] = answer
        choice = answer['choice']
        unresolved = [f for f in row['consistency_flags'] if not (choice == 'entailed' and f == 'operative_effect_requires_source_review')]
        row['unresolved_consistency_flags'] = unresolved
        row['candidate_status'] = ('observation_candidate_unverified' if choice == 'entailed' and not unresolved
            and row['types']['alignment'] == 'aligned' else 'needs_consistency_review')
        if choice == 'literal_only':
            row['ledger']['literal_context'] = {'binding': copy.deepcopy(row['binding']), 'status': 'literal_only'}
            row['ledger']['observation_obligation']['evidence_status'] = 'unassessed'
        row['ledger']['observation_obligation']['scope_review_status'] = choice
    return revised
