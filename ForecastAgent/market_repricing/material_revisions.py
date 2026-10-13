"""Append-only material/map/forecast vintages, separate from closed market tasks."""
import copy
import hashlib
import json
from pathlib import Path

from .common import digest, instant, read, write
from .tracking import creation_schedule

SCHEMA = 'market-material-forecast-revision-v1'


def freeze(bundle, task, clock, parent_refs, extra_pages=()):
    """New information gets a new clock; old graph and task bytes stay intact."""
    original = digest(bundle)
    child = copy.deepcopy(bundle)
    for page in extra_pages:
        url = page['url']
        if url in child.get('pages', {}) and digest(child['pages'][url]) != digest(page):
            raise ValueError('A later document cannot silently replace an existing source version')
        child.setdefault('pages', {})[url] = copy.deepcopy(page)
    for page in child.get('pages', {}).values():
        captured = page.get('retrieved_at_utc')
        if captured and instant(captured) > instant(clock):
            raise ValueError('Material captured after the new revision clock')
    child['request']['as_of_utc'] = clock
    child['request_hash'] = hashlib.sha256(json.dumps(child['request'], sort_keys=True).encode()).hexdigest()
    metadata = {'schema': SCHEMA, 'key': task['key'], 'material_frozen_at': clock,
        'parent_material_sha256': original, 'parent_refs': copy.deepcopy(parent_refs),
        'old_task_sha256': digest(task), 'old_forecast_sha256': digest(task.get('forecast')),
        'old_map_sha256': digest(bundle.get('research_loop', {}).get('current')),
        'price_schedule': creation_schedule(task), 'historical_cutoff_eligible': False,
        'old_tasks_reopened': False, 'budget_reset': False}
    child['market_material_revision'] = metadata
    metadata = metadata | {'revision_input_sha256': digest(child)}
    assert digest(bundle) == original
    return child, metadata


def forecast_eligibility(forecast, horizon, label):
    """Only a sealed prospective head and its exact target quote may be scored."""
    h = str(horizon)
    target = forecast.get('price_schedule', {}).get('targets', {}).get(h)
    if not target:
        return 'target_missing'
    if not forecast.get('sealed_at') or instant(forecast['sealed_at']) >= instant(target):
        return 'forecast_after_target'
    if not forecast.get('timely') or not forecast.get('eligible_for_model_metrics', True):
        return 'forecast_not_metric_eligible'
    if label.get('target_at') is None or instant(label['target_at']) != instant(target):
        return 'label_target_mismatch'
    if label.get('status') != 'quoted':
        return label.get('status', 'label_not_observed')
    if label.get('origin_role') != forecast['price_schedule']['origin_role']:
        return 'label_origin_mismatch'
    quote = label.get('quote', {})
    # Never accept a later snapshot as a missed target-window observation.
    if not quote.get('received_at') or instant(quote['received_at']) > instant(target):
        return 'late_quote'
    if (instant(target) - instant(quote['received_at'])).total_seconds() > label.get('window_minutes', 30) * 60:
        return 'quote_outside_window'
    return 'eligible'


def evaluate(index, tracking):
    rows = []
    for case in index['cases']:
        result = read(case['result_file'])
        if digest(result) != case['result_sha256']:
            raise ValueError('Indexed forecast revision changed')
        folder = Path(case['result_file']).parent
        for name, expected in (('frozen-input.json', result['material_sha256']),
                               ('graph-snapshot.json', result['graph_sha256'])):
            if (folder / name).exists() and digest(read(folder / name)) != expected:
                raise ValueError('Revision material or graph binding changed')
        forecast = result.get('forecast') or {}
        observed = tracking.get('tasks', {}).get(case['key'], {})
        for h, target in result['price_schedule']['targets'].items():
            label = observed.get('prices', {}).get(h, {})
            eligibility = forecast_eligibility(forecast, h, label)
            row = {'key': case['key'], 'revision_id': result['revision_id'],
                'family': result['family'], 'horizon_hours': int(h), 'target_at': target,
                'material_sha256': result['material_sha256'], 'graph_sha256': result['graph_sha256'],
                'forecast_sealed_at': forecast.get('sealed_at'), 'eligibility': eligibility, 'arms': {}}
            if eligibility == 'eligible':
                label_value = label['label']
                base = forecast['baseline'][h]
                row.update(label=label_value, baseline_error=base - label_value)
                for name, arm in forecast.get('arms', {}).items():
                    if arm.get('status') == 'completed' and h in arm.get('price_means', {}):
                        p = arm['price_means'][h]
                        row['arms'][name] = {'prediction': p, 'error': p - label_value}
            rows.append(row)
    import math
    from collections import Counter
    summary = {}
    for h in (24, 72, 168):
        pool = [r for r in rows if r['horizon_hours'] == h]
        paired = [r for r in pool if {'original', 'mapped'} <= set(r['arms'])]
        metrics = {}
        for arm in ('no_change', 'original', 'mapped'):
            errors = [r['baseline_error'] if arm == 'no_change' else r['arms'][arm]['error'] for r in paired]
            metrics[arm] = {'paired_n': len(errors), 'mae': sum(abs(e) for e in errors) / len(errors) if errors else None,
                'rmse': math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else None}
        summary[str(h)] = {'eligibility': dict(Counter(r['eligibility'] for r in pool)),
            'independent_event_families': len({r['family'] for r in paired}), 'paired_metrics': metrics}
    return {'schema': SCHEMA, 'price_origin': 'market_created_at', 'rows': rows, 'summary': summary,
        'late_forecasts_and_quotes_excluded': True, 'old_vintages_not_overwritten': True,
        'market_price_errors_are_not_event_brier_or_trading_pnl': True}


def publish(root, cases):
    """Latest pointers are replaceable; each referenced revision stays immutable."""
    root = Path(root)
    entries = []
    for folder in cases:
        path = Path(folder) / 'revision-result.json'
        if not path.exists():
            continue
        result = read(path)
        entries.append({'key': result['key'], 'revision_id': result['revision_id'],
            'result_file': str(path.resolve()), 'result_sha256': digest(result),
            'material_sha256': result['material_sha256'], 'graph_sha256': result['graph_sha256'],
            'map_status': result['map_status'], 'forecast_status': result.get('forecast', {}).get('status'),
            'price_schedule': result['price_schedule']})
    index = {'schema': SCHEMA, 'cases': entries, 'closed_task_state_unchanged': True,
        'price_origin': 'market_created_at', 'vintage_time_is_not_market_creation_time': True}
    write(root / 'revision-index.json', index)
    return index
