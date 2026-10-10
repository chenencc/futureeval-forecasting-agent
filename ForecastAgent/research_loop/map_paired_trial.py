"""Actual Super maps and paired Mercury forecasts over frozen original coverage."""
import argparse
import copy
import hashlib
import os
from pathlib import Path

from ForecastAgent.acquisition.pipeline import reject_outcomes, verify_baseline
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest, load, save, WARNING
from ForecastAgent.research_loop import POLICY, conditional, decision, decision_http, live_trial, state
from ForecastAgent.research_loop.forecast_brief_refs import blocks
from ForecastAgent.runtime.task_lock import task_lock

PROTOCOL = 'frozen-original-map-pair-v1'
ARMS = ('original', 'mapped', 'conditional')


def controls(parent):
    return {str(p.relative_to(parent)): live_trial.sha(p) for p in sorted(parent.rglob('*.json'))}


def receipts(root):
    return sorted(Path(root).rglob('http/*.json'))


def freeze_references(bundle, common):
    """Expose every visible row/block as an exact map reference, never hidden text."""
    sources = {s['source_id']: s for s in common['sources']}
    if len(sources) != len(common['sources']):
        raise ValueError('Duplicate common source IDs')
    windows = []
    for e in common['evidence']:
        source = sources[e['source_id']]
        body = bundle['pages'][source['url']]['content']
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        if (source['body_sha256'] != body_hash or type(e['start']) is not int or
                type(e['end']) is not int or not 0 <= e['start'] < e['end'] <= len(body) or
                body[e['start']:e['end']] != e['text']):
            raise ValueError('Common original text does not match saved body coordinates')
        for a, b in blocks(e['text']):
            if e['text'][a:b].strip():
                windows.append({'url': source['url'], 'body_sha256': body_hash,
                                'start': e['start']+a, 'end': e['start']+b})
    child = copy.deepcopy(bundle)
    if child.get('research_loop'):
        raise ValueError('Use fresh experimental map inputs; never renew an existing map ledger')
    child['research_map_visible_references'] = windows
    child['request']['research_state_policy'] = POLICY
    child['pipeline'] = 'collection'
    state.initialize(child)
    material, refs = live_trial.visible_catalog(child, common)
    if len(refs) != len(windows):
        raise ValueError('Visible map reference omitted from the common evidence')
    live_trial.map_original_view(child, common)
    return child, {'material_sha256': material['material_sha256'], 'reference_count': len(refs),
                   'common_state_sha256': digest(common), 'new_source_text': False}


def prepare(common, heads, spec, child=None, *, conditional_heads=False):
    """A/B have identical target heads. Conditional heads are a separate shadow arm."""
    target = 'event_yes' if spec is None else 'event_outcome'
    if set(heads) != {target}:
        raise ValueError('The paired arms require the same event-only registry')
    enriched = copy.deepcopy(common)
    map_audit = {'status': 'original_only', 'scoring_map_omitted': True}
    if child is not None:
        notes, map_audit = decision.scoring_map(child, common)
        if notes.get('factual_grounding_present'):
            enriched['research_map'] = notes
    if chain.request_bytes(enriched, heads) > decision.BYTE_CAP:
        enriched = copy.deepcopy(common)
        map_audit = {**map_audit, 'scoring_map_omitted': True, 'status': 'map_exceeds_frozen_request_cap'}
    plan = {'status': 'direct', 'reason': 'Matched target-only comparison', 'plan': None}
    registry = copy.deepcopy(heads)
    if conditional_heads and child is not None:
        plan = conditional.plan_for(child, {'enriched': enriched})
        if plan['status'] == 'single_pivot':
            enriched['scoring_plan'] = copy.deepcopy(plan['plan'])
            enriched['scoring_instruction'] = (
                'The pivot is an unverified world-event hypothesis. NOT(C) is its exact logical '
                'complement. Missing information does not establish nonoccurrence. Source confidence '
                'is not a branch weight. Original evidence and exact target rules control.')
            registry = conditional.registry_for(heads, plan)
            if chain.request_bytes(enriched, registry) > decision.BYTE_CAP:
                enriched.pop('scoring_plan'); enriched.pop('scoring_instruction')
                plan = {'status': 'direct', 'reason': 'Conditional heads exceed frozen cap; originals retained', 'plan': None}
                registry = copy.deepcopy(heads)
    if any(enriched[k] != v for k, v in common.items()):
        raise ValueError('Map changed common original evidence')
    if chain.request_bytes(enriched, registry) > decision.BYTE_CAP:
        raise ValueError('Common originals exceed scoring request cap')
    return {'state': enriched, 'questions': registry, 'required_questions': copy.deepcopy(heads),
            'spec': copy.deepcopy(spec), 'question': copy.deepcopy(common['question']), 'plan': plan,
            'audit': {'map': map_audit, 'original_state_sha256': digest(common),
                      'same_originals': True, 'request_bytes': chain.request_bytes(enriched, registry)}}


def score(prepared, folder):
    folder = Path(folder)
    save(folder/'prepared.json', prepared)
    response = decision_http.call(prepared['state'], folder/'decision', prepared['questions'],
                                  required_questions=prepared['required_questions'])
    result = conditional.evaluate(prepared, response)
    result['summary'] = live_trial.forecast_summary(result, prepared['question'])
    if 'payload' in result['conditional']:
        result['conditional']['summary'] = live_trial.forecast_summary(result['conditional'], prepared['question'])
    save(folder/'result.json', result)
    return result


def map_summary(child, prepared):
    current = child['research_loop'].get('current') or {}
    delivered = prepared['state'].get('research_map', {})
    nodes = current.get('nodes', [])
    return {'accepted_nodes': len(nodes), 'kinds': {k: sum(n['kind'] == k for n in nodes)
            for k in ('observation', 'driver', 'assumption', 'unknown')},
            'accepted_relations': len(current.get('relations', [])),
            'delivered_nodes': len(delivered.get('nodes', [])),
            'delivered_relations': len(delivered.get('relations', [])),
            'hidden_node_ids': delivered.get('hidden_node_ids', []),
            'grounded_map_delivered': bool(delivered.get('factual_grounding_present')),
            'score_plan_audit': child['research_loop']['events'][-1]['acceptance'].get('score_plan'),
            'meaning_verified': False}


def run(parent, root, *, execute=False, max_http=20):
    parent, root = Path(parent), Path(root)
    parents = load(parent/'parents.json')
    if not 1 <= len(parents) <= 5 or type(max_http) is not int or not 0 <= max_http <= 20:
        raise ValueError('Use 1-5 explicit cases and at most 20 physical attempts')
    verify_baseline()
    frozen = []
    for path in parents:
        bundle = load(path); ident = str(bundle['request']['id'])
        common_folder = parent/'live/cases'/ident
        common, heads, spec = [load(common_folder/(name+'.json')) for name in ('common-state', 'questions', 'spec')]
        reject_outcomes(common['question'])
        child, preflight = freeze_references(bundle, common)
        prepare(common, heads, spec)
        frozen.append((ident, path, bundle, common, heads, spec, child, preflight))
    if len({row[0] for row in frozen}) != len(frozen):
        raise ValueError('Duplicate case ID')
    parent_identity = controls(parent)
    identity = {'schema': PROTOCOL, 'parent': str(parent.resolve()), 'control_files': parent_identity,
                'implementation': decision.implementation_hashes(), 'max_http': max_http,
                'models': [live_trial.SUPER, decision_http.MODEL], 'map_protocol': conditional.PROTOCOL,
                'same_originals': True, 'same_primary_scoring_heads': True,
                'one_map_and_one_request_per_scoring_arm': True, 'brief_review_calls': 0,
                'primary_comparison': 'original-only versus original-plus-map target-only forecasts',
                'conditional_policy': 'separate shadow request only for a supported single pivot',
                'searches': 0, 'fetches': 0, 'submitted': False, 'old_quotas_reset': False}
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        if (root/'identity.json').exists() and load(root/'identity.json') != identity:
            raise ValueError('Frozen originals, implementation or budget changed')
        save(root/'identity.json', identity)
        rows = []
        for index, (ident, path, bundle, common, heads, spec, child, preflight) in enumerate(frozen):
            folder = root/'cases'/ident
            for name, value in [('common-state', common), ('questions', heads), ('spec', spec), ('reference-preflight', preflight)]:
                save(folder/(name+'.json'), value)
            row = {'id': ident, 'question': common['question']['question'],
                   'type': common['question']['question_type'], 'status': 'prepared', 'arms': {},
                   'preflight': preflight, 'source_bundle_sha256': live_trial.sha(path)}
            if execute:
                def room(stage, reserve=0):
                    cached = (stage/'decision/response.json').exists() or (stage/'accepted-state.json').exists()
                    if not cached and len(receipts(root))+1+reserve > max_http:
                        raise RuntimeError('Preserved experiment HTTP cap exhausted; no quota renewed')
                try:
                    room(folder/'original')
                    result = score(prepare(common, heads, spec), folder/'original')
                    row['arms']['original'] = {'status': 'completed', 'summary': result['summary']}
                except Exception as exc:
                    row['arms']['original'] = {'status': 'failed', 'error': type(exc).__name__+': '+str(exc)}
                try:
                    room(folder/'map', reserve=1)
                    child = live_trial.map_stage(child, common, folder/'map', os.environ['OPENROUTER_API_KEY'],
                        http_cap=1, map_protocol=conditional.PROTOCOL, frozen_scoring_coverage=True)
                    save(folder/'research-package.json', child)
                    row['map_status'] = 'completed'
                except Exception as exc:
                    row.update(map_status='failed', map_error=type(exc).__name__+': '+str(exc))
                mapped = prepare(common, heads, spec, child)
                delivered = bool(mapped['state'].get('research_map', {}).get('factual_grounding_present'))
                if row['map_status'] == 'completed':
                    row['map'] = map_summary(child, mapped)
                    save(folder/'graph.json', child['research_loop']['current'])
                if not delivered:
                    row['arms']['mapped'] = {**copy.deepcopy(row['arms']['original']),
                        'status': 'original_fallback' if row['arms']['original']['status'] == 'completed' else 'failed',
                        'new_request': False, 'reason': 'No grounded map delivered; paired result is not a second draw'}
                else:
                    try:
                        room(folder/'mapped')
                        result = score(mapped, folder/'mapped')
                        row['arms']['mapped'] = {'status': 'completed', 'summary': result['summary'], 'new_request': True}
                    except Exception as exc:
                        row['arms']['mapped'] = {'status': 'failed', 'error': type(exc).__name__+': '+str(exc)}
                pivot = prepare(common, heads, spec, child, conditional_heads=True)
                if pivot['plan']['status'] == 'single_pivot':
                    try:
                        remaining_primary = sum(
                            not (root/'cases'/r[0]/arm/'decision/response.json').exists()
                            for r in frozen[index+1:] for arm in ('original', 'mapped')) + len(frozen[index+1:])
                        room(folder/'conditional', reserve=remaining_primary)
                        result = score(pivot, folder/'conditional')
                        row['arms']['conditional'] = {'status': 'completed', 'batched_direct': result['summary'],
                                                     'shadow': result['conditional']}
                    except Exception as exc:
                        row['arms']['conditional'] = {'status': 'failed', 'error': type(exc).__name__+': '+str(exc)}
                else:
                    row['arms']['conditional'] = {'status': 'not_requested', 'reason': pivot['plan'].get('reason')}
                row['status'] = 'completed' if all(row['arms'][arm]['status'] in {'completed', 'original_fallback'}
                                                  for arm in ('original', 'mapped')) else 'incomplete'
            row['originals_preserved'] = (bundle['pages'] == child['pages'] and all(
                bundle.get(k) == child.get(k) for k in ('searches', 'exa_searches', 'fetch_attempts', 'model_attempts', 'extract_attempts')))
            if not row['originals_preserved'] or controls(parent) != parent_identity:
                raise ValueError('Frozen control material or acquisition ledgers changed')
            save(folder/'result.json', row); rows.append(row)
            usage = live_trial.usage(receipts(root))
            if usage['totals']['http_attempts'] > max_http:
                raise ValueError('Physical experiment cap exceeded')
            report = {'schema': PROTOCOL, 'rows': rows, 'cases': len(frozen),
                      'processed': len(rows), 'completed_pairs': sum(r['status'] == 'completed' for r in rows),
                      'usage': usage, 'actual_http': len(receipts(root)), 'max_http': max_http,
                      'controls_preserved': True, 'same_originals': True, 'same_primary_scoring_heads': True,
                      'brief_review_calls': 0, 'searches': 0, 'fetches': 0, 'submitted': False,
                      'accuracy': None, 'brier': None, 'evaluation_warning': WARNING,
                      'comparison_limit': 'One draw per arm. Forecast shifts do not establish improved quality without resolutions.'}
            save(root/'report.json', report)
            print({'id': ident, 'status': row['status'], 'map': row.get('map_status'),
                   'nodes': row.get('map', {}).get('accepted_nodes'), 'conditional': row['arms'].get('conditional', {}).get('status'),
                   'http': len(receipts(root))}, flush=True)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--max-http', type=int, default=20)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    run(args.parent, args.root, execute=args.execute, max_http=args.max_http)


if __name__ == '__main__':
    main()
