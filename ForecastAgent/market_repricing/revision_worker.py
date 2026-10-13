"""Bounded local update of frozen later materials; no acquisition or delivery."""
import argparse
import copy
import os
from pathlib import Path
import time

from .common import PublicHTTP, digest, now, policy, read, seal, write
from .material_revisions import SCHEMA, evaluate, publish


def execute(root, manifest):
    from .runtime import activate
    activate(manifest['engine_directory'])
    from ForecastAgent.research_loop import post_supplement
    from .materials import prepare
    from .forecast import run as score
    from .venues import refresh, book
    from .worker import provider_budget
    root = Path(root).resolve()
    expected = digest(manifest)
    if (root / 'run-identity.json').exists() and read(root / 'run-identity.json')['manifest_sha256'] != expected:
        raise ValueError('Frozen revision manifest changed')
    write(root / 'run-identity.json', {'schema': SCHEMA, 'manifest_sha256': expected,
        'new_model_cap': manifest['model_cap'], 'new_source_search_cap': 0, 'new_source_fetch_cap': 0})
    ledger_path = root / 'state.json'
    ledger = read(ledger_path) if ledger_path.exists() else copy.deepcopy(manifest['parent_worker_state'])
    initial_records = manifest['parent_worker_state'].get('provider_reservations', [])
    if ledger.get('provider_reservations', [])[:len(initial_records)] != initial_records:
        raise ValueError('Historical provider reservations changed')
    os.environ['GITHUB_RUN_ID'] = root.name
    cfg = policy()
    cfg['maximum_worker_model_attempts_per_dispatch'] = min(cfg['maximum_worker_model_attempts_per_dispatch'], manifest['model_cap'])
    public = PublicHTTP(root / 'current-market-http', cap=2 * len(manifest['cases']))
    folders = []
    for case in manifest['cases']:
        folder = root / 'cases' / case['task_hash']
        folders.append(folder)
        result_path = folder / 'revision-result.json'
        if result_path.exists():
            continue
        source = read(folder / 'frozen-input.json')
        if digest(source) != case['input_sha256']:
            raise ValueError('Frozen later materials changed')
        result = {'schema': SCHEMA, 'revision_id': manifest['revision_id'], 'key': case['key'],
            'family': case['market']['family'], 'material_sha256': case['input_sha256'],
            'material_frozen_at': source['request']['as_of_utc'], 'parent_refs': case['parent_refs'],
            'price_schedule': case['price_schedule'], 'old_task_sha256': case['old_task_sha256'],
            'map_status': 'not_started', 'graph_sha256': digest(None), 'forecast': {},
            'old_production_task_reopened': False, 'budget_reset': False}
        write(root / 'progress.json', {'status': 'running', 'key': case['key'],
            'finished_cases': sum((f / 'revision-result.json').exists() for f in folders), 'at': now()})
        try:
            with provider_budget(root, ledger, case['key'], cfg):
                view, admission = prepare(source)
                if not view['pages']:
                    raise ValueError('Latest material revision has no admitted originals')
                reviewed, review = post_supplement.run(view, folder / 'map-review', http_cap=2,
                    failure_cap=2, consistency_review=True, deadline=time.monotonic() + 360)
                fresh = review['final_revision'] > review['initial_revision']
                graph = reviewed['research_loop'].get('current')
                snapshot = {'schema': SCHEMA, 'key': case['key'], 'revision_id': manifest['revision_id'],
                    'material_input_sha256': case['input_sha256'], 'review_bundle_sha256': digest(reviewed),
                    'new_map_update_accepted': fresh, 'graph': graph, 'review_status': review['status'],
                    'graph_semantics_verified': False, 'material_processing': review.get('material_processing'),
                    'reading_coverage': review.get('reading_coverage'),
                    'pending_materials': review.get('pending_materials'),
                    'old_map_reference_only': not fresh, 'captured_originals_unchanged': True}
                write(folder / 'graph-snapshot.json', snapshot)
                result.update(map_status='updated' if fresh else 'prior_map_not_updated',
                    graph_sha256=digest(snapshot), map_file=str(folder / 'graph-snapshot.json'),
                    review_status=review['status'], admission=admission)
                # Later originals cannot retroactively borrow the old forecast's quote.
                anchor_path = folder / 'anchor.json'
                if anchor_path.exists():
                    current, anchor = read(folder / 'current-market.json'), read(anchor_path)
                else:
                    current, _ = refresh(public, case['market'])
                    write(folder / 'current-market.json', current)
                    if current['rules_sha256'] != case['market']['rules_sha256']:
                        raise ValueError('Contract rules changed; later forecast must not replace the original proposition')
                    if not current.get('tradable') or current.get('terminal_result'):
                        raise ValueError('Contract is no longer open; graph is retained without a prospective score')
                    anchor = book(public, current, cfg)
                    if anchor['status'] == 'shallow_book':
                        bid, ask = anchor.get('yes_bid'), anchor.get('yes_ask')
                        if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and 0 < bid <= ask < 1:
                            anchor.update(status='usable', midpoint=(bid + ask) / 2,
                                eligible_for_model_metrics=False, observational_context_only=True)
                    write(anchor_path, anchor)
                if anchor['status'] != 'usable':
                    raise ValueError('No usable current quote for a later forecast')
                score_cfg = copy.deepcopy(cfg) | {'price_schedule': case['price_schedule']}
                if not fresh:
                    score_cfg['analysis_arms'] = ['original']
                forecast = score(reviewed, current, anchor, folder / 'forecast', score_cfg)
                if anchor.get('eligible_for_model_metrics') is False:
                    forecast.update(eligible_for_model_metrics=False, observational_context_only=True)
                    write(folder / 'forecast/result.json', forecast)
                result['forecast'] = forecast
        except Exception as exc:
            result.update(error_type=type(exc).__name__, error=str(exc)[:350])
        finally:
            result['completed_at'] = now()
            write(result_path, result)
            index = publish(root, folders)
            write(root / 'state.json', ledger)
            write(root / 'progress.json', {'status': 'running', 'finished_cases': len(index['cases']),
                'total_cases': len(manifest['cases']), 'at': now()})
            # Shared-account exhaustion stops the remaining queue without a key switch.
            new = ledger.get('provider_reservations', [])[len(initial_records):]
            if new and new[-1].get('http_status') in {401, 402, 403, 429}:
                write(root / 'progress.json', {'status': 'blocked', 'reason': 'account_or_quota',
                    'finished_cases': len(index['cases']), 'total_cases': len(manifest['cases']), 'at': now()})
                seal(root)
                return index
    index = publish(root, folders)
    write(root / 'progress.json', {'status': 'completed', 'finished_cases': len(index['cases']),
        'total_cases': len(manifest['cases']), 'at': now()})
    seal(root)
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--evaluate-tracking')
    args = parser.parse_args()
    root = Path(args.root)
    if args.evaluate_tracking:
        result = evaluate(read(root / 'revision-index.json'), read(args.evaluate_tracking))
        write(root / 'revision-metrics.json', result)
    else:
        result = execute(root, read(root / 'manifest.json'))
    print(__import__('json').dumps({'root': str(root), 'cases': len(result.get('cases', [])),
        'progress': read(root / 'progress.json') if (root / 'progress.json').exists() else None}))


if __name__ == '__main__':
    main()
