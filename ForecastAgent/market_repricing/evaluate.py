"""Paired errors with explicit attrition, event grouping and no trade claims."""
import math
from collections import Counter
from pathlib import Path

from .common import digest, policy, read, state, write


def run(monitor_root, worker_root, output):
    from .tracking import forecast_schedule, final_record
    from .common import instant
    monitor, worker = read(Path(monitor_root) / 'state.json'), read(Path(worker_root) / 'state.json')
    cfg = policy()
    # An exact approved admission migration can occur in the worker before the
    # next monitor tick. Use its in-memory view without rewriting old labels or
    # source receipts. Unknown policies, engine drift and quota changes fail closed.
    if monitor['identity']['policy_sha256'] != digest(cfg):
        monitor = state(monitor_root, 'monitor', cfg)
    if worker['identity']['policy_sha256'] != digest(cfg):
        worker = state(worker_root, 'worker', cfg)
    if monitor['identity']['policy_sha256'] != worker['identity']['policy_sha256']:
        raise ValueError('Evaluation policies differ')
    if digest(cfg) != worker['identity']['policy_sha256']:
        raise ValueError('Evaluation configuration drift')
    rows, summary, event_rows = [], {}, []
    for key, task in worker['tasks'].items():
        forecast = task.get('forecast')
        labels_path = Path(monitor_root) / 'labels' / (digest(key) + '.json')
        labels = read(labels_path) if labels_path.exists() else {}
        targets = forecast_schedule(forecast)['targets'] if forecast else {str(h): None for h in cfg['horizons_hours']}
        for h_text, expected in targets.items():
            h = int(h_text)
            label = labels.get(str(h), {'status': 'not_due_or_unobserved'})
            if forecast and label.get('target_at') and instant(label['target_at']) != instant(expected):
                label = {'status': 'label_belongs_to_different_anchor', 'excluded_from_repricing_mae': True}
            row = {'key': key, 'family': task['market']['family'], 'horizon_hours': h,
                   'target_at': expected, 'origin_role': forecast_schedule(forecast)['origin_role'] if forecast else None,
                   'task_status': task['status'], 'label_status': label['status'], 'arms': {},
                   'map_delivered': bool(forecast and forecast.get('pair_audit', {}).get('map_delivered'))}
            if (forecast and forecast.get('eligible_for_model_metrics', True) and
                task.get('evidence_status') != 'invalid_original_quarantined' and
                forecast.get('timely') and expected and label['status'] == 'quoted'):
                target, base = label['label'], forecast['baseline'][str(h)]
                row.update(target=target, baseline=base, baseline_error=base-target)
                for name, arm in forecast['arms'].items():
                    if arm['status'] == 'completed' and arm.get('timely', forecast['timely']) and instant(arm.get('sealed_at', forecast['sealed_at'])) < instant(expected):
                        if str(h) not in arm.get('price_means', {}):
                            continue
                        p = arm['price_means'][str(h)]
                        flat = cfg['flat_band']
                        direction = lambda x: 0 if abs(x) < flat else (1 if x > 0 else -1)
                        row['arms'][name] = {'prediction': p, 'error': p-target,
                                              'direction_correct': direction(p-base) == direction(target-base)}
            rows.append(row)
        market = monitor['markets'].get(key, task['market'])
        final = final_record(market, task['market']['rules_sha256'])
        event_row = {'key': key, 'final': final, 'arms': {}}
        if forecast and forecast.get('timely') and forecast.get('eligible_for_model_metrics', True) and final['eligible_for_event_brier']:
            y = 1 if final['result'] == 'yes' else 0
            for name, arm in forecast.get('arms', {}).items():
                seal = arm.get('sealed_at', forecast['sealed_at'])
                resolution_at = final.get('resolved_at') or market.get('terminal_seen_at') or final['observed_at']
                if arm.get('status') == 'completed' and arm.get('timely', forecast['timely']) and instant(seal) < instant(resolution_at) and arm.get('eventual_probability_yes') is not None:
                    event_row['arms'][name] = {'probability': arm['eventual_probability_yes'], 'brier': (arm['eventual_probability_yes']-y)**2}
        event_rows.append(event_row)
    cohorts = sorted({(r['origin_role'], r['horizon_hours']) for r in rows}, key=str)
    all_names = list(dict.fromkeys(cfg['analysis_arms'] + [name for t in worker['tasks'].values()
        for name in t.get('forecast', {}).get('arms', {})]))
    comparisons = {'original_vs_mapped': ['original', 'mapped']}
    if 'official_map' in all_names:
        comparisons.update(original_vs_official_map=['original', 'official_map'],
                           three_arm=['original', 'mapped', 'official_map'])
    for origin, h in cohorts:
        pool = [r for r in rows if r['horizon_hours'] == h and r['origin_role'] == origin]
        names = cfg['analysis_arms']
        paired = [r for r in pool if r['map_delivered'] and all(n in r['arms'] for n in names)]
        metrics = {}
        for name in ['no_change'] + names:
            errors = [r['baseline_error'] if name == 'no_change' else r['arms'][name]['error'] for r in paired]
            metrics[name] = {'paired_n': len(errors), 'mae': sum(abs(x) for x in errors)/len(errors) if errors else None,
                             'rmse': math.sqrt(sum(x*x for x in errors)/len(errors)) if errors else None}
        comparison_metrics = {}
        for comparison, comparison_names in comparisons.items():
            complete = [r for r in pool if all(n in r['arms'] for n in comparison_names)]
            comparison_metrics[comparison] = {'independent_event_families': len({r['family'] for r in complete}),
                'metrics': {name: {'paired_n': len(complete),
                    'mae': sum(abs(r['baseline_error'] if name == 'no_change' else r['arms'][name]['error']) for r in complete)/len(complete) if complete else None,
                    'rmse': math.sqrt(sum((r['baseline_error'] if name == 'no_change' else r['arms'][name]['error'])**2 for r in complete)/len(complete)) if complete else None}
                for name in ['no_change'] + comparison_names}}
        summary[str(origin) + ':' + str(h)] = {'label_status_distribution': dict(Counter(r['label_status'] for r in pool)),
                            'map_unavailable_count': sum(not r['map_delivered'] for r in pool),
                            'independent_event_families': len({r['family'] for r in paired}), 'paired_metrics': metrics,
                            'comparisons': comparison_metrics}
    result = {'protocol': 'market-repricing-evaluation-v2', 'event_rows': event_rows,
              'event_brier': {name: {'n': len(values := [r['arms'][name]['brier'] for r in event_rows if name in r['arms']]), 'brier': sum(values)/len(values) if values else None} for name in all_names},
              'legacy_and_creation_cohorts_separate': True, 'rows': rows, 'summary': summary,
              'selection_and_missing_labels_retained': True, 'eventual_event_brier_is_separate': True,
              'no_trades_or_pnl_claims': True, 'direction_flat_band': cfg['flat_band']}
    write(output, result)
    return result
