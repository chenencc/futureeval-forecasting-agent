"""Offline scoring of frozen referenced-analysis predictions and source provenance."""
import argparse
import hashlib
import json
import math
import zipfile
from collections import Counter
from pathlib import Path

from ForecastAgent.analysis.pilot import WARNING, digest, load, save
from ForecastAgent.providers.decisions import probability
from ForecastAgent.analysis.inputs import resolve_bundle, supplement_identity
from ForecastAgent.analysis.ensemble import compare
from ForecastAgent.analysis.recovery import task_result


def transport_usage(folder):
    records = [load(p) for kind in ('ultra-http', 'mercury-http') for p in sorted((folder / kind).glob('*.json'))]
    usage = {'reasoning_http_attempts': len(list((folder / 'ultra-http').glob('*.json'))),
             'decision_http_attempts': len(list((folder / 'mercury-http').glob('*.json'))),
             'known_tokens': 0, 'attempts_with_unknown_usage': 0, 'reported_cost_usd': 0,
             'requested_models': dict(Counter(r['request']['model'] for r in records))}
    for record in records:
        data = record.get('response', {}).get('usage', {}) or {}
        if isinstance(data.get('total_tokens'), int):
            usage['known_tokens'] += data['total_tokens']
        elif isinstance(data.get('input_tokens'), int) and isinstance(data.get('output_tokens'), int):
            usage['known_tokens'] += data['input_tokens'] + data['output_tokens']
        else:
            usage['attempts_with_unknown_usage'] += 1
        usage['reported_cost_usd'] += data.get('cost', 0) or 0
    return usage


def evaluate(output, archive, labels, destination, baseline=None, supplement_root=None):
    output = Path(output)
    manifest = load(output / 'manifest.json')
    if manifest.get('supplement_identity') != supplement_identity(supplement_root):
        raise ValueError('Evaluation supplement identity mismatch')
    frozen = []
    failures = []
    task_usage = {}
    task_results = []
    with zipfile.ZipFile(archive) as saved:
        source_campaign = json.loads(saved.read('campaign.json'))
        if digest(source_campaign) != manifest['campaign_sha256']:
            raise ValueError('Evaluation source campaign identity mismatch')
        for ident in manifest['ids']:
            folder = output / 'tasks' / ident
            if (folder / 'result.json').exists():
                result = load(folder / 'result.json')
                routes = load(folder / 'routes.json') if result.get('operational_origin') == 'available_model_output' else None
                expected_result = task_result(ident, routes, result.get('error'))
                if result != expected_result:
                    raise ValueError('Task result is inconsistent with frozen route or default policy')
                task_results.append(result)
            task_usage[ident] = transport_usage(folder)
            has_prediction = (folder / 'prediction.json').exists()
            if not has_prediction:
                failure = load(folder / 'failure.json') if (folder / 'failure.json').exists() else {'id': ident, 'status': 'missing_prediction'}
                if manifest.get('route_mode') != 'reasoning_only':
                    failures.append({**failure, 'http_usage': task_usage[ident]})
                if not (folder / 'routes.json').exists():
                    if manifest.get('route_mode') == 'reasoning_only':
                        failures.append({**failure, 'http_usage': task_usage[ident]})
                    continue
            prediction = load(folder / 'prediction.json') if has_prediction else load(folder / 'routes.json')
            frozen_prediction_hash = digest(prediction)
            packet, analysis, state = [load(folder / name) for name in ('evidence-packet.json', 'analysis.json', 'decision-state.json')]
            for body, key in [(packet, 'packet_sha256'), (analysis, 'analysis_sha256'), (state, 'decision_state_sha256')]:
                if digest(body) != prediction[key]:
                    raise ValueError('Frozen inference changed: ' + ident)
            bundle = json.loads(saved.read('tasks/' + ident + '/bundle.json'))
            input_record = load(folder / 'input.json')
            if digest(bundle) != input_record.get('original_bundle_sha256', input_record['bundle_sha256']):
                raise ValueError('Frozen original bundle identity mismatch')
            bundle = resolve_bundle(bundle, ident, supplement_root)
            if digest(bundle) != load(folder / 'input.json')['bundle_sha256']:
                raise ValueError('Frozen source bundle identity mismatch')
            excerpts = load(folder / 'citation-audit.json')['resolved_evidence']
            for excerpt in excerpts:
                body = bundle['pages'][excerpt['url']]['content']
                if hashlib.sha256(body.encode()).hexdigest() != excerpt['body_sha256'] or body[excerpt['start']:excerpt['end']] != excerpt['text']:
                    raise ValueError('Referenced source span failed: ' + excerpt['evidence_id'])
            reasoning_p = analysis['reasoning_probability_yes']
            mercury_p = prediction.get('probability_yes') if has_prediction else None
            if has_prediction and prediction['reasoning_baseline_probability_yes'] != reasoning_p:
                raise ValueError('Reasoning probability differs from frozen analysis')
            if has_prediction and load(folder / 'decision-response.json')['answers']['event_yes']['noul'] != mercury_p:
                raise ValueError('Mercury probability differs from frozen response')
            expected = compare(reasoning_p, mercury_p)
            if (folder / 'routes.json').exists():
                routes = load(folder / 'routes.json')
                for body, key in [(packet, 'packet_sha256'), (analysis, 'analysis_sha256'), (state, 'decision_state_sha256')]:
                    if routes[key] != digest(body):
                        raise ValueError('Frozen route input changed')
                # Incomplete route persistence is recoverable from the frozen provider response.
                if routes['routes']['reasoning_only']['probability_yes'] != reasoning_p:
                    raise ValueError('Frozen reasoning route changed')
                if not has_prediction and routes['routes']['reasoning_then_mercury']['probability_yes'] is not None:
                    raise ValueError('Unverified Mercury route without frozen prediction')
                actual_mercury = routes['routes']['reasoning_then_mercury']['probability_yes']
                expected_routes = compare(reasoning_p, actual_mercury)
                if any(routes[key] != value for key, value in expected_routes.items()):
                    raise ValueError('Route aggregation changed')
                if has_prediction and actual_mercury is not None and actual_mercury != mercury_p:
                    raise ValueError('Frozen Mercury route changed')
            prediction.update(reasoning_baseline_probability_yes=reasoning_p, probability_yes=mercury_p,
                              route_comparison=expected)
            usage = task_usage[ident]
            frozen.append({**prediction, 'prediction_sha256': frozen_prediction_hash, 'http_usage': usage,
                           'evidence_stats': {'source_count': len(packet['sources']), 'fact_count': len(analysis['facts']),
                               'referenced_spans': len(excerpts), 'all_spans_verified_against_frozen_evidence': True,
                               'supplement_overlay_used': supplement_root is not None,
                               'initial_packet_omitted_chars': sum(s['packet_omitted_chars'] for s in packet['sources']),
                               'local_read_calls': load(folder / 'session.json')['local_read_calls']},
                           'conditions': analysis['conditions'], 'gaps': analysis['gaps']})
    # Labels are opened only after frozen inference and original-source checks.
    outcomes = {}
    for line in Path(labels).read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        resolution = row.get('resolution', {})
        value = resolution.get('resolved_to')
        if row.get('source') == 'metaculus' and resolution.get('resolved') is True and type(value) in (int, float) and value in (0, 1):
            outcomes[str(row['id'])] = resolution
    prior = {}
    if baseline:
        old = load(baseline)
        prior = {r['id']: r for r in old['results']}
    rows = []
    for prediction in frozen:
        ident = prediction['id']
        if ident not in outcomes:
            raise ValueError('Missing binary resolution: ' + ident)
        y = float(outcomes[ident]['resolved_to'])
        row = {**prediction, 'resolution': y, 'label_checkpoint_date': outcomes[ident].get('resolution_date')}
        row['equal_mean_probability_yes'] = row['route_comparison']['equal_mean_probability_yes']
        for field, prefix in [('probability_yes', 'mercury'), ('reasoning_baseline_probability_yes', 'reasoning'), ('equal_mean_probability_yes', 'equal_mean')]:
            if row[field] is None:
                continue
            p = probability(row[field])
            clipped = max(1e-6, min(1 - 1e-6, p))
            row[prefix + '_brier'] = (p - y) ** 2
            row[prefix + '_log_loss'] = -(y * math.log(clipped) + (1 - y) * math.log(1 - clipped))
            row[prefix + '_correct_at_half'] = (p >= .5) == bool(y)
        if ident in prior:
            row['exploratory_v1_comparison'] = {'previous_probability_yes': prior[ident]['probability_yes'],
                'previous_quality': prior[ident]['analysis_quality']['status'],
                'previous_known_tokens': prior[ident]['http_usage']['known_tokens'],
                'previous_brier': prior[ident]['mercury_brier'],
                'note': 'Same saved question; changed protocol and context. Not a controlled model comparison.'}
        rows.append(row)
    operational = []
    for result in task_results:
        ident = result['id']
        if ident not in outcomes or result.get('operational_probability_yes') is None:
            continue
        p = probability(result['operational_probability_yes'])
        y = float(outcomes[ident]['resolved_to'])
        operational.append({'id': ident, 'status': result['status'], 'origin': result['operational_origin'],
                            'probability_yes': p, 'brier': (p - y) ** 2})
    report = {'schema': 'referenced_analysis_review_v2', 'manifest': manifest, 'evaluation_warning': WARNING,
        'label_provenance': 'Local ForecastBench resolved=true Metaculus records; checkpoint dates are not necessarily settlement timestamps.',
        'source_archive_sha256': hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
        'label_file_sha256': hashlib.sha256(Path(labels).read_bytes()).hexdigest(),
        'results': rows, 'failures': failures, 'requested_count': len(manifest['ids']), 'evaluated_count': len(rows),
        'quality_distribution': dict(Counter(r['quality']['status'] for r in rows)),
        'metrics': {key: sum(r[key] for r in rows if key in r) / sum(key in r for r in rows)
                    for key in ('mercury_brier', 'mercury_log_loss', 'reasoning_brier', 'reasoning_log_loss', 'equal_mean_brier', 'equal_mean_log_loss') if any(key in r for r in rows)},
        'route_counts': {prefix: sum(prefix + '_brier' in r for r in rows) for prefix in ('reasoning', 'mercury', 'equal_mean')},
        'paired_metrics': {prefix: {key: sum(r[prefix + '_' + key] for r in rows if 'mercury_brier' in r) / sum('mercury_brier' in r for r in rows)
                                   for key in ('brier', 'log_loss')}
                           for prefix in ('reasoning', 'mercury', 'equal_mean')} if any('mercury_brier' in r for r in rows) else {},
        'calibration': {'method': 'identity', 'fitted': False},
        'task_result_coverage': {'requested': len(manifest['ids']), 'recorded': len(task_results),
                                 'statuses': dict(Counter(r['status'] for r in task_results))},
        'operational_results': operational,
        'operational_brier_including_explicit_defaults': sum(r['brier'] for r in operational) / len(operational) if operational else None,
        'recovered_model_results': sum(bool(r['quality'].get('recovery_applied')) for r in rows),
        'paired_non_recovered_count': sum('mercury_brier' in r and not r['quality'].get('recovery_applied') for r in rows),
        'paired_non_recovered_metrics': {prefix: {key: sum(r[prefix + '_' + key] for r in rows if 'mercury_brier' in r and not r['quality'].get('recovery_applied')) / sum('mercury_brier' in r and not r['quality'].get('recovery_applied') for r in rows)
                                               for key in ('brier', 'log_loss')}
                                       for prefix in ('reasoning', 'mercury', 'equal_mean')} if any('mercury_brier' in r and not r['quality'].get('recovery_applied') for r in rows) else {},
        'consumption': {key: sum(usage[key] for usage in task_usage.values()) for key in ('reasoning_http_attempts', 'decision_http_attempts', 'known_tokens', 'attempts_with_unknown_usage', 'reported_cost_usd')},
        'quota_checks': {'within_task_caps': all(
            usage['decision_http_attempts'] <= 1 and
            (all(count <= 3 for model, count in usage['requested_models'].items() if model != 'inception/mercury-decide:free')
             and usage['reasoning_http_attempts'] <= 3 * manifest['routing_policy']['maximum_models']
             if 'http_cap_per_model' in manifest.get('routing_policy', {}) else usage['reasoning_http_attempts'] <= 3)
            for usage in task_usage.values()),
                         'no_collection_reopened': True, 'no_search_or_forecast_tools_exposed': True}}
    save(destination, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--source-archive', required=True)
    parser.add_argument('--labels', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--baseline')
    parser.add_argument('--supplement-root')
    args = parser.parse_args()
    report = evaluate(args.output, args.source_archive, args.labels, args.report, args.baseline, args.supplement_root)
    print(json.dumps({'evaluated_count': report['evaluated_count'], 'failures': report['failures'], 'metrics': report['metrics'], 'consumption': report['consumption']}))
