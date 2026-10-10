"""One optional world-event pivot; direct scoring remains the primary output."""
import argparse
import copy
import json
import math
from pathlib import Path

from ForecastAgent.analysis.pilot import digest, load, save, WARNING
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.providers.decisions import probability, validate
from ForecastAgent.research_loop import forecast_map, decision, decision_http
from ForecastAgent.research_loop.distribution import forecast as distribution
from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.runtime.task_lock import task_lock

PROTOCOL = 'forecast-score-map-v4'
SCORING_PROTOCOL = 'single-pivot-shadow-v1'
MAX_PLAN_BYTES = 2400
DISAGREEMENT = .15
MIN_PIVOT_USABILITY = .65
PLAN_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['mode', 'reason', 'pivot'], 'properties': {
        'mode': {'type': 'string', 'enum': ['direct', 'single_pivot']},
        'reason': {'type': 'string', 'maxLength': 180},
        'pivot': {'type': 'object', 'additionalProperties': False,
            'required': ['kind', 'entity', 'event', 'time_window', 'evidence_node_ids', 'mechanism'],
            'properties': {
                'kind': {'type': 'string', 'enum': ['world_event', 'none']},
                'entity': {'type': 'string', 'maxLength': 100},
                'event': {'type': 'string', 'maxLength': 220},
                'time_window': {'type': 'string', 'maxLength': 120},
                'evidence_node_ids': {'type': 'array', 'maxItems': 3,
                    'items': {'type': 'string', 'maxLength': 40}},
                'mechanism': {'type': 'string', 'maxLength': 200}}}}}
TOOL = copy.deepcopy(forecast_map.TOOL)
TOOL['function']['parameters']['properties']['score_plan'] = PLAN_SCHEMA
TOOL['function']['parameters']['required'].append('score_plan')
SYSTEM = forecast_map.SYSTEM + '''

In the SAME tool call add a SMALL score_plan, never numerical probabilities.
Default to mode=direct if no useful pivotal world event is supported. Use
pivot.kind=none, all its string fields empty and evidence_node_ids=[] for direct.
For single_pivot choose at most ONE uncertain world event C. Define its entity,
observable criterion (metric/threshold when applicable), and explicit time window.
The program constructs NOT(C); do not invent a third 'unknown' outcome or a second
branch. C and NOT(C) are the complete two-way partition under the SAME definition.
Do not use information availability, publication-not-yet-found, inaccessible pages,
source reliability or the target Y itself as the pivot. An unpublished future target
is normally a gap, not a reason for a low forecast. A pivot must materially change
the target through an explicit mechanism and refer to 1-3 retained observation IDs.
Baselines, plans and forecasts may inform C but do not prove a realized outcome.
Avoid a pivot already fixed by the evidence. A good direct plan is preferable to
a forced scenario. This optional plan is a fallible hypothesis; bad plans are
isolated without discarding literal evidence or requesting a format correction.
'''


def plan_audit(proposal, nodes):
    """Validate shape and bound coordinates, never causal truth or relevance."""
    report = {'schema': SCORING_PROTOCOL, 'proposal': copy.deepcopy(proposal),
        'proposal_sha256': digest(proposal), 'status': 'rejected', 'plan': None,
        'meaning_verified': False, 'partition': 'C / logical NOT(C)',
        'extra_model_calls': 0, 'errors': []}
    try:
        check_schema(proposal, PLAN_SCHEMA)
        if not proposal['reason'].strip():
            raise ValueError('A scoring plan needs a nonempty reason')
        pivot = proposal['pivot']
        if proposal['mode'] == 'direct':
            if pivot['kind'] != 'none' or pivot['evidence_node_ids'] or any(
                    pivot[k] for k in ('entity', 'event', 'time_window', 'mechanism')):
                raise ValueError('Direct plan must have an empty pivot')
            report.update(status='direct', plan=copy.deepcopy(proposal))
        else:
            if pivot['kind'] != 'world_event' or any(not pivot[k].strip() for k in
                    ('entity', 'event', 'time_window', 'mechanism')):
                raise ValueError('A world-event pivot needs entity, criterion, window and mechanism')
            refs = pivot['evidence_node_ids']
            bound = {n['id'] for n in nodes if n['kind'] == 'observation' and n.get('bindings')}
            if not refs or len(set(refs)) != len(refs) or not set(refs) <= bound:
                raise ValueError('Pivot must refer only to unique retained bound observations')
            report.update(status='coordinate_valid_unverified', plan=copy.deepcopy(proposal))
    except (ValueError, KeyError, TypeError) as exc:
        report['errors'].append(str(exc))
    return report


def plan_for(bundle, prepared):
    """Only the current journal's plan and scoring-visible nodes are eligible."""
    events = bundle.get('research_loop', {}).get('events', [])
    review = events[-1].get('acceptance', {}).get('score_plan') if events else None
    if not review:
        return {'status': 'direct', 'reason': 'No current optional scoring plan', 'plan': None}
    if review['status'] != 'coordinate_valid_unverified':
        return {'status': 'direct', 'reason': review['status'], 'plan': None,
                'plan_audit': copy.deepcopy(review)}
    plan = copy.deepcopy(review['plan'])
    visible = {n['id'] for n in prepared['enriched'].get('research_map', {}).get('nodes', [])}
    if not set(plan['pivot']['evidence_node_ids']) <= visible:
        return {'status': 'direct', 'reason': 'Pivot observation omitted from delivered map', 'plan': None}
    return {'status': 'single_pivot', 'plan': plan, 'meaning_verified': False}


def registry_for(base, plan):
    """Batch the direct target and optional conditional heads in one request."""
    target = 'event_yes' if 'event_yes' in base else 'event_outcome'
    questions = {target: copy.deepcopy(base[target])}
    if plan['status'] != 'single_pivot':
        return questions
    questions['pivot_yes'] = {'type': 'noul', 'instructions':
        'Estimate P(C | all supplied original evidence E). C is defined EXACTLY by '
        'state.scoring_plan.pivot entity, event and time_window. It is a world event, '
        'not whether C has been observed or evidence was found. Do not score Y here. '
        'Use plans/baselines as evidence, not completed outcomes.'}
    for key, holds in [('target_given_pivot', True), ('target_given_not_pivot', False)]:
        questions[key] = copy.deepcopy(questions[target])
        questions[key]['instructions'] = (
            'Estimate the CONDITIONAL target forecast P(Y | ' + ('C' if holds else 'NOT(C)') + ', E). '
            'Assume the exact world event in state.scoring_plan.pivot ' +
            ('HOLDS' if holds else 'DOES NOT HOLD') + ' for this head only, while keeping ALL '
            'other supplied evidence and the exact target rules. Estimate a conditional '
            'probability/distribution, NOT the joint P(Y and C), NOT a branch weight. '
            'Other decision-head answers are unavailable. ' + questions[target]['instructions'])
    questions['pivot_usable'] = {'type': 'noul', 'instructions':
        'How likely is the proposed pivot a usable, nontrivial uncertain WORLD EVENT '
        'with an unambiguous entity/criterion/time window, grounded background and a '
        'material mechanism for the exact target? Information missing, publication '
        'unavailable, a tautological copy of Y, wrong entity/period, or an already '
        'settled condition are not usable pivots. This is a FALLIBLE plan diagnostic, '
        'not an event probability or a weight on the forecast.'}
    return questions


def prepare(bundle):
    """Freeze original coverage before adding heads; never reduce text to fit."""
    prepared = decision.prepare(bundle)
    plan = plan_for(bundle, prepared)
    state = copy.deepcopy(prepared['enriched'])
    questions = registry_for(prepared['questions'], plan)
    if plan['status'] == 'single_pivot':
        state['scoring_plan'] = copy.deepcopy(plan['plan'])
        state['scoring_instruction'] = ('The pivot is an unverified scenario hypothesis. '
            'Original evidence and exact rules control. NOT(C) is the exact logical '
            'complement, not an unknown-information branch. Do not multiply source '
            'confidence or marginal events; do not infer nonoccurrence from gaps.')
    from ForecastAgent.analysis.mercury_evidence_chain import request_bytes
    if (len(json.dumps(state.get('scoring_plan', {})).encode()) > MAX_PLAN_BYTES or
            request_bytes(state, questions) > decision.BYTE_CAP):
        plan = {'status': 'direct', 'reason': 'Conditional request exceeds the frozen byte cap', 'plan': None}
        state = copy.deepcopy(prepared['enriched'])
        questions = registry_for(prepared['questions'], plan)
    if request_bytes(state, questions) > decision.BYTE_CAP:
        raise ValueError('Direct request exceeds the common byte cap')
    if any(state[k] != v for k, v in prepared['baseline'].items()):
        raise ValueError('Conditional plan changed the frozen original evidence')
    target = 'event_yes' if prepared['spec'] is None else 'event_outcome'
    return {'state': state, 'questions': questions, 'required_questions': {target: questions[target]},
        'plan': plan, 'spec': prepared['spec'], 'question': copy.deepcopy(bundle['request']),
        'audit': {**prepared['audit'], 'scoring_protocol': SCORING_PROTOCOL,
            'original_state_sha256': digest(prepared['baseline']),
            'request_bytes': request_bytes(state, questions), 'primary_policy': 'direct',
            'disagreement_threshold': DISAGREEMENT, 'minimum_pivot_usability': MIN_PIVOT_USABILITY,
            'thresholds_validated_on_outcomes': False}}


def raw_forecast(response, key, spec):
    if spec is None:
        return {'probability_yes': probability(response['answers'][key]['noul'])}
    adapted = {'answers': {'event_outcome': response['answers'][key]}}
    result = distribution(adapted, spec)
    return ({'probability_yes_per_category': result['probabilities']} if spec['kind'] == 'multiple_choice'
            else {'continuous_cdf': result['raw_cdf']})


def mixture(weight, yes, no):
    """Exact two-way marginalization; no internal clipping or independence claim."""
    weight = probability(weight)
    if set(yes) != set(no) or len(yes) != 1:
        raise ValueError('Branch forecast types differ')
    key = next(iter(yes))
    def mix(a, b):
        return probability(math.fsum((weight * probability(a), (1-weight) * probability(b))))
    if key == 'probability_yes':
        return {key: mix(yes[key], no[key])}
    if key == 'probability_yes_per_category':
        if set(yes[key]) != set(no[key]):
            raise ValueError('Branch option identities differ')
        return {key: {name: mix(yes[key][name], no[key][name]) for name in yes[key]}}
    if key != 'continuous_cdf' or len(yes[key]) != len(no[key]):
        raise ValueError('Branch CDF grids differ')
    if any(any(a > b for a, b in zip(row[key], row[key][1:])) for row in (yes, no)):
        raise ValueError('Branch CDF is not monotone')
    return {key: [mix(a, b) for a, b in zip(yes[key], no[key])]}


def distance(a, b):
    key = next(iter(a))
    if key == 'probability_yes':
        return abs(a[key]-b[key])
    if key == 'probability_yes_per_category':
        return .5 * math.fsum(abs(a[key][name]-b[key][name]) for name in a[key])
    return max(abs(x-y) for x, y in zip(a[key], b[key]))


def evaluate(prepared, response):
    """Optional head errors cannot invalidate a valid direct target forecast."""
    validate(response, prepared['required_questions'])
    target = next(iter(prepared['required_questions']))
    direct = raw_forecast(response, target, prepared['spec'])
    direct_payload = payload(prepared['question'], direct)
    result = {'schema': SCORING_PROTOCOL, 'status': 'completed', 'primary_policy': 'direct',
        'raw_forecast': direct, 'payload': direct_payload, 'direct':
            {'raw_forecast': direct, 'payload': direct_payload},
        'conditional': {'status': 'not_requested', 'reason': prepared['plan'].get('reason')},
        'review_required': False, 'review_http_attempts': 0, 'conditional_reread': False,
        'submitted': False, 'audit': prepared['audit'], 'evaluation_warning': WARNING}
    if prepared['plan']['status'] != 'single_pivot':
        return result
    result['conditional'] = {'status': 'invalid_optional_answers', 'plan': prepared['plan']['plan'],
        'meaning_verified': False, 'error': None}
    try:
        optional = {k: v for k, v in prepared['questions'].items() if k != target}
        validate(response, optional)
        w = probability(response['answers']['pivot_yes']['noul'])
        yes = raw_forecast(response, 'target_given_pivot', prepared['spec'])
        no = raw_forecast(response, 'target_given_not_pivot', prepared['spec'])
        combined = mixture(w, yes, no)
        combined_payload = payload(prepared['question'], combined)
        delta = distance(direct, combined)
        usable = probability(response['answers']['pivot_usable']['noul'])
        reasons = []
        if usable < MIN_PIVOT_USABILITY:
            reasons.append('pivot_semantic_usability')
        if delta >= DISAGREEMENT:
            reasons.append('direct_conditional_disagreement')
        result['conditional'].update(status='needs_review' if reasons else 'shadow_candidate',
            weight=w, branch_C=yes, branch_not_C=no, raw_forecast=combined,
            payload=combined_payload, direct_distance=delta, distance_metric=
                'absolute_probability' if prepared['spec'] is None else
                'total_variation' if prepared['spec']['kind'] == 'multiple_choice' else 'max_cdf_distance',
            pivot_usability=usable, review_reasons=reasons,
            formula='P(Y|E)=P(C|E)*P(Y|C,E)+(1-P(C|E))*P(Y|NOT(C),E)',
            source_reliability_used_as_weight=False, branch_probabilities_clipped=False)
        result['review_required'] = bool(reasons)
    except (ValueError, KeyError, TypeError) as exc:
        result['conditional']['error'] = str(exc)
    return result


def run(bundle, folder, *, execute=False):
    """One exact cached Mercury request. Shadow scores never replace the primary."""
    prepared = prepare(bundle)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    identity = {'protocol': SCORING_PROTOCOL, 'source_sha256': digest(bundle),
        'prepared_sha256': digest(prepared), 'implementation': decision.implementation_hashes(),
        'http_cap': 1, 'primary_policy': 'direct', 'submitted': False}
    with task_lock(folder):
        if (folder/'identity.json').exists() and load(folder/'identity.json') != identity:
            raise ValueError('Frozen scoring plan, evidence, implementation or policy changed')
        save(folder/'identity.json', identity)
        save(folder/'prepared.json', prepared)
        if not execute:
            return {'status': 'prepared', 'provider_attempts': 0, 'plan': prepared['plan'], 'audit': prepared['audit']}
        try:
            response = decision_http.call(prepared['state'], folder/'decision', prepared['questions'],
                required_questions=prepared['required_questions'])
            result = evaluate(prepared, response)
        except Exception as exc:
            save(folder/'failure.json', {'status': 'failed', 'error': type(exc).__name__+': '+str(exc),
                'fabricated_forecast': False, 'quota_reset': False, 'submitted': False})
            raise
        save(folder/'result.json', result)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    print(run(load(args.bundle), args.root, execute=args.execute)['status'])


if __name__ == '__main__':
    main()
