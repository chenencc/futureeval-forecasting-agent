"""Paired errors with explicit attrition, event grouping and no trade claims."""
import math
from collections import Counter
from pathlib import Path

from .common import digest, policy, read, write


def run(monitor_root, worker_root, output):
    monitor, worker = read(Path(monitor_root) / 'state.json'), read(Path(worker_root) / 'state.json')
    if monitor['identity']['policy_sha256'] != worker['identity']['policy_sha256']:
        raise ValueError('Evaluation policies differ')
    cfg = policy()
    if digest(cfg) != worker['identity']['policy_sha256']:
        raise ValueError('Evaluation configuration drift')
    rows, summary = [], {}
    for key, task in worker['tasks'].items():
        forecast = task.get('forecast')
        labels_path = Path(monitor_root) / 'labels' / (digest(key) + '.json')
        labels = read(labels_path) if labels_path.exists() else {}
        for h in cfg['horizons_hours']:
            label = labels.get(str(h), {'status': 'not_due_or_unobserved'})
            row = {'key': key, 'family': task['market']['family'], 'horizon_hours': h,
                   'task_status': task['status'], 'label_status': label['status'], 'arms': {},
                   'map_delivered': bool(forecast and forecast.get('pair_audit', {}).get('map_delivered'))}
            if forecast and forecast.get('timely') and label['status'] == 'quoted':
                target, base = label['label'], forecast['baseline'][str(h)]
                row.update(target=target, baseline=base, baseline_error=base-target)
                for name, arm in forecast['arms'].items():
                    if arm['status'] == 'completed':
                        p = arm['price_means'][str(h)]
                        flat = cfg['flat_band']
                        direction = lambda x: 0 if abs(x) < flat else (1 if x > 0 else -1)
                        row['arms'][name] = {'prediction': p, 'error': p-target,
                                              'direction_correct': direction(p-base) == direction(target-base)}
            rows.append(row)
    for h in cfg['horizons_hours']:
        pool = [r for r in rows if r['horizon_hours'] == h]
        names = cfg['analysis_arms']
        paired = [r for r in pool if r['map_delivered'] and all(n in r['arms'] for n in names)]
        metrics = {}
        for name in ['no_change'] + names:
            errors = [r['baseline_error'] if name == 'no_change' else r['arms'][name]['error'] for r in paired]
            metrics[name] = {'paired_n': len(errors), 'mae': sum(abs(x) for x in errors)/len(errors) if errors else None,
                             'rmse': math.sqrt(sum(x*x for x in errors)/len(errors)) if errors else None}
        summary[str(h)] = {'label_status_distribution': dict(Counter(r['label_status'] for r in pool)),
                            'map_unavailable_count': sum(not r['map_delivered'] for r in pool),
                            'independent_event_families': len({r['family'] for r in paired}), 'paired_metrics': metrics}
    result = {'protocol': 'market-repricing-evaluation-v1', 'rows': rows, 'summary': summary,
              'selection_and_missing_labels_retained': True, 'eventual_event_brier_is_separate': True,
              'no_trades_or_pnl_claims': True, 'direction_flat_band': cfg['flat_band']}
    write(output, result)
    return result
