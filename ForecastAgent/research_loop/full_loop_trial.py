"""Bounded native acquisition-to-map trial with no forecasting stage."""
import argparse
import copy
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.acquisition import pipeline
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.research_loop import fusion_trial, grounding, live_trial, state
from ForecastAgent.runtime.task_lock import task_lock

PROTOCOL = 'native-acquisition-map-loop-v1'
BUDGET = {**fusion_trial.BUDGET, 'score_http': 0}


def input_for(parent, when, *, gap_feedback=False):
    request = fusion_trial.input_for(parent, when, 'fusion')
    request[grounding.FIELD] = grounding.POLICY
    request['recover_sources_before_stall'] = True
    request['collection_purpose'] = 'Native acquisition-map feedback trial; no scoring or submission'
    if gap_feedback:
        from ForecastAgent.research_loop import delta, delivery, reading_views
        from ForecastAgent.research_loop import gap_feedback as receipts
        request.update({delta.FIELD: delta.POLICY, delivery.FIELD: delivery.POLICY,
                        reading_views.FIELD: reading_views.POLICY, receipts.FIELD: receipts.POLICY})
    return request


def feedback_audit(bundle, package=None):
    """Count only chronologically later, exact-body observation bindings."""
    revisions = bundle.get('research_loop', {}).get('events', [])
    actions = bundle.get('research_acquisition', {}).get('events', [])
    rows = []
    for action in actions:
        for body in action.get('new_bodies', []):
            if not body.get('usable_text'):
                continue
            bindings = []
            finished = action.get('completed_at_utc')
            for event in revisions:
                if (not finished or not event.get('at_utc') or
                        event['revision'] <= action['map_revision'] or
                        datetime.fromisoformat(event['at_utc']) < datetime.fromisoformat(finished)):
                    continue
                for node in event['state']['nodes']:
                    if node['kind'] != 'observation':
                        continue
                    for ref in node.get('bindings', []):
                        if (ref['url'], ref['body_sha256']) == (body['url'], body['body_sha256']):
                            bindings.append({'revision': event['revision'], 'node_id': node['id'],
                                             'claim': node['claim'], 'evidence_id': ref['evidence_id']})
            rows.append({'action_index': action['index'], 'tool': action['tool'],
                         'action_map_revision': action['map_revision'],
                         'intent_node_ids': action.get('research_node_ids', []), **body,
                         'later_observation_bindings': bindings,
                         'closed_body_binding_loop': bool(bindings)})
    raw_sources = state.catalog(bundle)['sources']
    final_sources = state.catalog(package if package is not None else bundle)['sources']
    added = [ref for url, ref in final_sources.items()
             if raw_sources.get(url, {}).get('body_sha256') != ref['body_sha256']]
    return {'body_events': rows,
            'post_map_actions': sum(e.get('map_revision', 0) > 0 for e in actions),
            'post_map_linked_actions': sum(e.get('map_revision', 0) > 0 and bool(e.get('research_node_ids')) for e in actions),
            'post_map_readable_body_events': sum(r['action_map_revision'] > 0 for r in rows),
            'post_map_body_events_bound_later': sum(r['action_map_revision'] > 0 and r['closed_body_binding_loop'] for r in rows),
            'initial_body_events_bound_later': sum(r['action_map_revision'] == 0 and r['closed_body_binding_loop'] for r in rows),
            'supplement_added_or_changed_readable_bodies': added,
            'supplement_is_outside_agent_feedback_loop': True,
            'body_binding_does_not_verify_relevance': True}


def summarize(bundle, package=None):
    final = package if package is not None else bundle
    result = bundle.get('result') or {}
    report = result.get('material_report') or {}
    return {'acquisition': fusion_trial.acquisition_summary(final),
            'feedback': feedback_audit(bundle, final),
            'map_audit': state.audit(copy.deepcopy(final)),
            'stop': {k: result.get(k) for k in ('status', 'termination_reason', 'session_state', 'incomplete', 'resumable')},
            'execution_report': result.get('execution_report'),
            'unresolved_material_targets': report.get('unresolved_material_targets'),
            'map_material_requests': (bundle.get('research_loop', {}).get('current') or {}).get('material_requests', []),
            'gap_nodes': [n for n in (bundle.get('research_loop', {}).get('current') or {}).get('nodes', []) if n['kind'] == 'unknown'],
            'scores_generated': False, 'submitted': False}


def verify_caps(summary):
    acquisition = summary['acquisition']
    fields = {'super_http': 'model_http', 'tavily_basic': 'tavily_basic', 'exa': 'exa_search',
              'initial_fetch_reservations': 'initial_fetch', 'extract_batches': 'extract_batches',
              'map_revisions': 'map_updates'}
    for name, cap in fields.items():
        if acquisition[name] > BUDGET[cap]:
            raise ValueError('Frozen cap exceeded: ' + name)


def run(parents, root, *, execute=False, limit_cases=None, continue_from=None, gap_feedback=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not 1 <= len(parents) <= 5:
        raise ValueError('Use one to five explicit source snapshots')
    if limit_cases is not None and (type(limit_cases) is not int or not 1 <= limit_cases <= len(parents)):
        raise ValueError('Case limit must be within the frozen input list')
    if os.environ.get('FORECAST_MODEL') != live_trial.SUPER or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '0':
        raise ValueError('Freeze Super directly with model fallback disabled')
    with task_lock(root):
        old = load(root/'identity.json') if (root/'identity.json').exists() else None
        parent_root = Path(continue_from).resolve() if continue_from else None
        parent_identity = load(parent_root/'identity.json') if parent_root else None
        if parent_root and (parent_root == root.resolve() or parent_identity['budget'] != BUDGET or
                            parent_identity['model'] != live_trial.SUPER):
            raise ValueError('Continuation requires a distinct root with unchanged model and budget')
        when = old['started_at_utc'] if old else parent_identity['started_at_utc'] if parent_identity else datetime.now(timezone.utc).isoformat()
        inputs = [{'id': str(load(p)['request']['id']), 'source': str(Path(p).resolve()),
                   'source_sha256': live_trial.sha(p), 'request': input_for(load(p), when, gap_feedback=gap_feedback)} for p in parents]
        if len({i['id'] for i in inputs}) != len(inputs):
            raise ValueError('Duplicate question ID')
        identity = {'schema': PROTOCOL, 'started_at_utc': when, 'inputs': inputs, 'budget': BUDGET,
                    'candidate_sha256': pipeline.identity(inputs[0]['request'], True)['candidate_sha256'],
                    'model': live_trial.SUPER, 'old_quotas_reset': False,
                    'provider_slots': json.loads(os.environ.get('FORECAST_TRIAL_PROVIDER_SLOTS', '{}')),
                    'scope': 'Fresh live sources, native loop through acquisition closure; no early map stop',
                    'comparison': 'Previous saved-body trials are interface references, not full-loop controls',
                    'scoring_enabled': False, 'submission_enabled': False}
        seeds = {}
        if parent_root:
            if inputs != parent_identity['inputs']:
                raise ValueError('Continuation cannot change question fields or original inputs')
            for entry in inputs:
                source_folder = parent_root/'cases'/entry['id']
                seed = source_folder/'acquisition/collection/bundle.json'
                if not seed.exists():
                    continue
                saved = load(seed)
                if not (saved.get('result') or {}).get('incomplete'):
                    raise ValueError('Never reopen a closed trial task')
                if any(a.get('status') == 'reserved' for a in saved.get('model_attempts', [])):
                    raise ValueError('Unknown physical model receipt requires review before migration')
                seeds[entry['id']] = {'bundle_sha256': live_trial.sha(seed),
                    'files': {str(p.relative_to(seed.parent)): live_trial.sha(p)
                              for p in sorted(seed.parent.rglob('*')) if p.is_file()},
                    'clock_sha256': live_trial.sha(source_folder/'collection-clock.json')}
            identity['continuation'] = {'root': str(parent_root), 'identity_sha256': live_trial.sha(parent_root/'identity.json'),
                                        'seeds': seeds, 'clocks_and_ledgers_preserved': True}
        if old and old != identity:
            raise ValueError('Frozen inputs, implementation or budget changed')
        save(root/'identity.json', identity)
        for ident, seed_record in seeds.items():
            target = root/'cases'/ident
            marker = target/'migration.json'
            if marker.exists():
                if load(marker) != seed_record:
                    raise ValueError('Migration receipt changed')
                continue
            source_folder = parent_root/'cases'/ident
            if (target/'acquisition/collection').exists():
                raise ValueError('Unconfirmed migration; preserve state instead of copying over it')
            shutil.copytree(source_folder/'acquisition/collection', target/'acquisition/collection')
            shutil.copy2(source_folder/'collection-clock.json', target/'collection-clock.json')
            if any(live_trial.sha(target/'acquisition/collection'/name) != checksum
                   for name, checksum in seed_record['files'].items()):
                raise ValueError('Migration checksum mismatch')
            save(marker, seed_record)
        save(root/'preregistration.json', {
            'primary_measures': ['post-map linked acquisition', 'new body bound in a later map revision',
                                 'useful dated baselines and leading indicators', 'explicit terminal gaps and usage'],
            'original_saved_bodies_reused': False, 'labels_used': False,
            'future_target_result_is_not_expected_before_publication': True,
            'semantic_review_required': True, 'budgets': BUDGET})
        rows = []
        halt = None
        for entry in inputs[:limit_cases]:
            folder = root/'cases'/entry['id']
            saved_result = folder/'trial-result.json'
            if saved_result.exists():
                row = load(saved_result)
            elif not execute:
                row = {'id': entry['id'], 'status': 'prepared'}
            else:
                save(folder/'input.json', entry['request'])
                path = folder/'acquisition/collection/bundle.json'
                existing = load(path) if path.exists() else {}
                attempts = existing.get('model_attempts', [])
                decisions = sum(a.get('status') == 'received' for a in attempts)
                clock = folder/'collection-clock.json'
                if not clock.exists():
                    save(clock, {'started_at_utc': datetime.now(timezone.utc).isoformat()})
                started = datetime.fromisoformat(load(clock)['started_at_utc'])
                remaining = BUDGET['seconds_per_arm'] - (datetime.now(timezone.utc)-started).total_seconds()
                row = {'id': entry['id'], 'question': entry['request']['question']}
                try:
                    # Delegate the entire native loop. A saved map is not a stop condition.
                    with fusion_trial.bounded_dispatch(len(attempts), decisions, len(attempts)-decisions, remaining):
                        report = pipeline.run(entry['request'], folder/'acquisition', supplement_network=True)
                    package_path = folder/'acquisition/package.json'
                    raw = load(path)
                    package = load(package_path) if package_path.exists() else None
                    summary = summarize(raw, package)
                    verify_caps(summary)
                    row.update(status='exported' if package is not None else 'preserved_incomplete',
                               summary=summary, pipeline_state=report['state'], resources=report['resources'])
                    save(folder/'graph.json', raw.get('research_loop', {}).get('current'))
                except Exception as exc:
                    row.update(status='failed', error_type=type(exc).__name__)
                    if path.exists():
                        row['summary'] = summarize(load(path))
                attention = fusion_trial.provider_attention(folder)
                if attention:
                    halt = {'reason': 'provider_attention_required', 'records': attention}
                    row['provider_attention'] = halt
                save(saved_result, row)
                print(json.dumps({'id': entry['id'], 'status': row['status'],
                                  'stop': row.get('summary', {}).get('stop')}, ensure_ascii=False), flush=True)
            rows.append(row)
            halt = row.get('provider_attention') or halt
            if any(live_trial.sha(i['source']) != i['source_sha256'] for i in inputs):
                raise ValueError('Original snapshot changed')
            if halt:
                break
        receipts = sorted(root.rglob('model_calls/*.json'))
        report = {'schema': PROTOCOL, 'requested': len(inputs), 'processed': len(rows), 'rows': rows,
                  'halt': halt, 'usage': live_trial.usage(receipts), 'budget': BUDGET,
                  'scoring_enabled': False, 'submitted': False, 'old_snapshots_preserved': True,
                  'all_cases_terminal_for_this_trial': execute and len(rows) == len(inputs),
                  'fresh_live_sources': True, 'controlled_accuracy_comparison': False}
        save(root/'report.json', report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parents', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--limit-cases', type=int)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--continue-from', type=Path, help='Audited interrupted-state migration; preserve clocks and cumulative caps')
    args = parser.parse_args()
    result = run(load(args.parents), args.root, execute=args.execute, limit_cases=args.limit_cases, continue_from=args.continue_from)
    print({k: result[k] for k in ('requested', 'processed', 'halt', 'usage', 'scoring_enabled')}, flush=True)


if __name__ == '__main__':
    main()
