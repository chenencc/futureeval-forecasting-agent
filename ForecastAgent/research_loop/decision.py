"""One Mercury scoring attempt over unchanged original evidence plus a map."""
import copy
import hashlib
import json
from pathlib import Path

from ForecastAgent.acquisition.handoff import uncovered
from ForecastAgent.acquisition.pipeline import reject_outcomes, verify_baseline
from ForecastAgent.analysis import mercury_evidence_chain as chain, mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.analysis.pilot import digest, load, save, WARNING
from ForecastAgent.competition.mercury import distribution_spec
from ForecastAgent.releases.context_decisions import select_first
from ForecastAgent.research_loop import VERSION
from ForecastAgent.research_loop.state import audit
from ForecastAgent.research_loop.distribution import forecast as stable_forecast
from ForecastAgent.runtime.task_lock import task_lock

BYTE_CAP = chain.SECOND_BYTES
MAP_BYTE_CAP = 5600


def scoring_map(bundle, baseline):
    """Map bindings cannot introduce raw text absent from the comparison arm."""
    report = audit(bundle)
    if not report['usable']:
        return {'status': report['status'], 'instruction': 'No current usable map. Score original evidence directly.'}, report
    current = bundle['research_loop']['current']
    from ForecastAgent.research_loop import simple_map
    simple = simple_map.is_source_first(bundle)
    if simple and report.get('gap_only'):
        return {'status': 'original_only_gap_inventory',
            'instruction': 'Gap-only map is retained in the audit. Score original evidence directly.'}, {**report, 'scoring_map_omitted': True}
    sources = {s['source_id']: s for s in baseline['sources']}
    nodes, hidden = [], []
    invalid = set(report.get('invalid_node_ids', []))
    for node in current['nodes']:
        bindings = node['bindings']
        visible = all(not uncovered(ref['start'], ref['end'],
            [(span['start'], span['end']) for span in baseline['evidence']
             if sources.get(span['source_id'], {}).get('url') == ref['url'] and
                sources.get(span['source_id'], {}).get('body_sha256') == ref['body_sha256']]) for ref in bindings)
        if not visible or node['id'] in invalid:
            hidden.append(node['id'])
            continue
        fields = ('id', 'kind', 'claim', 'event_time', 'time_status', 'gap_reason')
        if simple:
            fields += tuple(k for k in simple_map.NODE_SCHEMA['properties'] if k not in fields and k != 'evidence_ids')
        nodes.append({**{k: node[k] for k in fields if k in node},
            'bindings': [{k: ref[k] for k in ('url', 'body_sha256', 'start', 'end')} for ref in bindings]})
    def assemble(selected, omitted):
        kept = {node['id'] for node in selected}
        result = {'schema': VERSION,
            'status': 'unverified_gap_inventory' if report.get('gap_only') else 'unverified_interpretations',
            'factual_grounding_present': any(n['kind'] == 'observation' for n in selected), 'nodes': selected,
            'unreviewed_material_change': report['unreviewed_material_change'],
            'relations': [r for r in current['relations'] if {r['from_id'], r['to_id']} <= kept],
            'supporting_path': 'Recheck supporting interpretation from delivered nodes and original text.' if omitted else current['supporting_path'],
            'alternative_path': 'Recheck alternative interpretation from original text; omissions are not negative evidence.' if omitted else current['alternative_path'],
            'material_requests': [n for n in current['material_requests'] if set(n['node_ids']) <= kept],
            'hidden_node_ids': omitted,
            'instruction': 'All notes, claims and arrows are unverified hypotheses. Original text and exact resolution rules control the answer. Recheck entity, stage, metric, units, period, exceptions and contrary evidence. Gaps, failed fetches and future publications never establish nonoccurrence. Do not multiply node probabilities. You may reject this entire map. Binding coordinates point into the original evidence already supplied; missing map nodes are not negative evidence.'}
        if simple:
            result.update(map_protocol=bundle['research_loop']['events'][-1]['acceptance']['map_protocol'],
                evidence_groups=simple_map.grouping(selected))
        return result
    result = assemble(nodes, hidden)
    if len(json.dumps(result).encode()) > MAP_BYTE_CAP:
        # Pack complete nodes, prioritizing grounded observations; raw text is fixed.
        ordered = sorted(nodes, key=lambda n: (n['kind'] != 'observation', not n['bindings']))
        chosen = []
        for node in ordered:
            trial = chosen + [node]
            omitted = hidden + [n['id'] for n in nodes if n['id'] not in {x['id'] for x in trial}]
            candidate = assemble(trial, omitted)
            if len(json.dumps(candidate).encode()) <= MAP_BYTE_CAP:
                chosen = trial
        hidden += [n['id'] for n in nodes if n['id'] not in {x['id'] for x in chosen}]
        result = assemble(chosen, hidden)
        report = {**report, 'size_omitted_node_ids': hidden}
    if not result['nodes'] or (not report.get('gap_only') and
            not any(n['kind'] == 'observation' for n in result['nodes'])):
        result = {'schema': VERSION, 'status': 'map_omitted_size_or_coverage_limit',
            'instruction': 'No complete grounded observation delivered. Score original evidence directly.'}
        report = {**report, 'scoring_map_omitted': True}
    return result, report


def prepare(bundle):
    # Inspection must not mutate the archived parent or its selection identity.
    bundle = copy.deepcopy(bundle)
    reject_outcomes(bundle['request'])
    verify_baseline()
    audit(bundle)
    kind = bundle['request']['question_type']
    if kind not in {'binary', 'multiple_choice', 'numeric', 'date', 'discrete'}:
        raise ValueError('Unsupported question type')
    spec = None if kind == 'binary' else distribution_spec(bundle['request'])
    if kind == 'multiple_choice':
        payload(bundle['request'], {'probability_yes_per_category':
            {o: 1 / len(bundle['request']['options']) for o in bundle['request']['options']}})
    elif spec is not None:
        count = spec['meta']['inbound_outcome_count']
        payload(bundle['request'], {'continuous_cdf': [i / count for i in range(count + 1)]})
    registry = chain.questions() if spec is None else typed.questions(spec)
    baseline, selection = select_first(bundle, registry)
    enriched = copy.deepcopy(baseline)
    notes, map_audit = scoring_map(bundle, baseline)
    if notes.get('status') != 'original_only_gap_inventory':
        enriched['research_map'] = notes
    if chain.request_bytes(enriched, registry) > BYTE_CAP:
        enriched['research_map'] = {'status': 'map_omitted_size_limit',
            'instruction': 'Score original evidence directly; no map was delivered.'}
        map_audit['scoring_map_omitted'] = True
    if chain.request_bytes(enriched, registry) > BYTE_CAP:
        raise ValueError('Original evidence request exceeds the common scoring cap')
    if any(baseline[key] != enriched[key] for key in baseline):
        raise ValueError('Research map changed frozen original evidence or instructions')
    return {'baseline': baseline, 'enriched': enriched, 'questions': registry, 'spec': spec,
        'audit': {'source_selection': selection, 'map': map_audit,
            'equal_original_coverage': True, 'baseline_bytes': chain.request_bytes(baseline, registry),
            'enriched_bytes': chain.request_bytes(enriched, registry), 'common_byte_cap': BYTE_CAP,
            'baseline_state_sha256': digest(baseline), 'enriched_state_sha256': digest(enriched),
            'no_fresh_retrieval': True, 'evaluation_warning': WARNING}}


def implementation_hashes():
    root = Path(__file__).parent
    return {p.name: hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for p in sorted(root.glob('*.py'))}


def run(bundle, directory, *, execute=False, arm='enriched'):
    """Never submits; dry run prepares both equally covered arms without HTTP."""
    if arm not in {'baseline', 'enriched'}:
        raise ValueError('Unknown scoring arm')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    prepared = prepare(bundle)
    identity = {'schema': VERSION, 'source_bundle_sha256': digest(bundle),
        'prepared_sha256': digest(prepared), 'implementation_sha256': implementation_hashes(),
        'http_cap_per_arm': 1, 'evaluation_warning': WARNING, 'submission_enabled': False}
    with task_lock(directory):
        if (directory/'identity.json').exists() and load(directory/'identity.json') != identity:
            raise ValueError('Frozen evidence, map, implementation or scoring policy changed')
        save(directory/'identity.json', identity)
        for name in ('baseline', 'enriched', 'questions', 'spec', 'audit'):
            save(directory/(name+'.json'), prepared[name])
        if not execute:
            return {'status': 'prepared', 'provider_attempts': 0, 'submitted': False,
                    'audit': prepared['audit']}
        from ForecastAgent.research_loop import forecast_map, decision_http
        protocol = bundle.get('research_loop', {}).get('events', [])
        reviewed = bool(protocol and protocol[-1].get('acceptance', {}).get('map_protocol') in
                        {forecast_map.PROTOCOL, 'forecast-score-map-v4'})
        scoring_call = decision_http.call if reviewed else chain.call
        response = scoring_call(prepared[arm], directory/arm, prepared['questions'])
        question = bundle['request']
        if question['question_type'] == 'binary':
            raw = {'probability_yes': response['answers']['event_yes']['noul']}
            raw_score = raw['probability_yes']
        else:
            forecast = stable_forecast(response, prepared['spec'])
            raw = {'probability_yes_per_category': forecast['probabilities']} if question['question_type'] == 'multiple_choice' else {'continuous_cdf': forecast['raw_cdf']}
            raw_score = raw
        candidate = payload(question, raw)
        router = chain.route if question['question_type'] == 'binary' else typed.route
        result = {'schema': VERSION, 'status': 'completed', 'arm': arm, 'raw_score': raw_score,
            'payload': candidate, 'remaining_diagnostics': router(response),
            'conditional_reread': False, 'submitted': False, 'evaluation_warning': WARNING,
            'audit': prepared['audit']}
        if question['question_type'] != 'binary':
            result['distribution_arithmetic'] = forecast['arithmetic_audit']
        save(directory/(arm+'-result.json'), result)
        return result
