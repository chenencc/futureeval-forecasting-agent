"""Preregistered six-error and six-control saved-evidence analysis pilot."""
import argparse
from pathlib import Path

from ForecastAgent.analysis import mercury_coverage_chain as chain
from ForecastAgent.analysis.ensemble import metrics
from ForecastAgent.analysis.pilot import WARNING, digest, load, save

ERROR_IDS = ['40701', '40967', '43435', '43615', '44358', '44693']
CONTROL_IDS = ['14025', '43492', '43501', '44363', '24819', '36871']
IDS = ERROR_IDS + CONTROL_IDS


def run(inputs, output, dry_run=False):
    inputs, output = Path(inputs), Path(output)
    paths = {p.parent.name: p for p in inputs.glob('repaired-fifty-analysis-*/tasks/*/analysis-input.json')}
    if not set(IDS) <= set(paths):
        raise ValueError('All twelve frozen baseline inputs required')
    identity = {'protocol': chain.PROTOCOL, 'ids': IDS, 'error_ids': ERROR_IDS, 'control_ids': CONTROL_IDS,
                'bundle_sha256': {i: digest(load(paths[i])) for i in IDS},
                'dry_run': dry_run, 'model': chain.baseline.decisions.MODEL,
                'http_cap_per_task': 2, 'labels_loaded_by_inference': False}
    if (output/'manifest.json').exists() and load(output/'manifest.json') != identity:
        raise ValueError('Frozen pilot input changed')
    save(output/'manifest.json', identity)
    rows = []
    for ident in IDS:
        task = output/'tasks'/ident
        bundle = load(paths[ident])
        save(task/'analysis-input.json', bundle)
        # The retained baseline is evaluation data only, never model state.
        row = {'id': ident, 'bundle_sha256': digest(bundle)}
        try:
            row.update(chain.run_task(bundle, task/'mercury', dry_run=dry_run))
        except Exception as exc:
            row.update(status='failed', error=str(exc))
        save(task/'outcome.json', row)
        rows.append(row)
        save(output/'report.json', {'rows': rows, 'evaluation_warning': WARNING,
             'retrieval_calls': 0, 'submissions': 0})
        print(ident, row['status'], flush=True)


def evaluate(inputs, output, labels):
    inputs, output = Path(inputs), Path(output)
    manifest = load(output/'manifest.json')
    if manifest['ids'] != IDS or manifest['dry_run']:
        raise ValueError('A completed frozen twelve-case pilot is required')
    rows, attempts = [], []
    for ident in IDS:
        task = output/'tasks'/ident
        row = load(task/'outcome.json')
        old_path = list(inputs.glob('repaired-fifty-analysis-*/tasks/'+ident+'/outcome.json'))
        if len(old_path) != 1:
            raise ValueError('Unique baseline record required')
        old = load(old_path[0])
        if row['bundle_sha256'] != old['bundle_sha256'] or row['bundle_sha256'] != digest(load(task/'analysis-input.json')):
            raise ValueError('Evidence changed across paired analysis')
        if row['status'] == 'completed':
            stage = 'second' if row['second_call_required'] else 'first'
            response = chain.baseline.decisions.validate(load(task/'mercury'/stage/'response.json'), chain.questions())
            if row['probability_yes'] != response['answers']['event_yes']['noul']:
                raise ValueError('Prediction changed')
            row['baseline_probability_yes'] = old['clipped_probability_yes']
        rows.append(row)
        for path in (task/'mercury').glob('*/http/*.json'):
            record = load(path)
            usage = record.get('response', {}).get('usage', {}) or {}
            total = usage.get('total_tokens')
            if total is None and all(isinstance(usage.get(k), int) for k in ['input_tokens', 'output_tokens']):
                total = usage['input_tokens'] + usage['output_tokens']
            attempts.append({'id': ident, 'status': record['status'], 'tokens': total})
    frozen = digest(rows)
    outcomes = {r['id']: r['resolved_to'] for r in load(labels)['labels']}
    scored = []
    for row in rows:
        if row['status'] != 'completed':
            continue
        y = outcomes[row['id']]
        scored.append({**row, 'resolution': y,
                       'new': metrics(row['clipped_probability_yes'], y),
                       'old': metrics(row['baseline_probability_yes'], y)})
    def aggregate(items):
        summary = {'n': len(items)}
        if items:
            summary.update({route: {k: sum(r[route][k] for r in items)/len(items)
                           for k in ('brier', 'log_loss', 'correct_at_half')}
                           for route in ('old', 'new')})
        return summary
    report = {'protocol': chain.PROTOCOL, 'output_sha256_before_labels': frozen,
              'evaluation_warning': WARNING, 'error_selected_development_set': True,
              'same_saved_bodies_different_selection_and_instructions': True,
              'metrics': aggregate(scored),
              'errors': aggregate([r for r in scored if r['id'] in ERROR_IDS]),
              'controls': aggregate([r for r in scored if r['id'] in CONTROL_IDS]),
              'http_attempts': len(attempts), 'known_tokens': sum(r['tokens'] or 0 for r in attempts),
              'unknown_usage_attempts': sum(r['tokens'] is None for r in attempts),
              'rows': scored, 'failures': [r for r in rows if r['status'] != 'completed'],
              'retrieval_calls': 0, 'submissions': 0, 'automatic_use_eligible': False}
    save(output/'metrics.json', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run', 'evaluate'])
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--labels')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.command == 'run':
        run(args.inputs, args.output, args.dry_run)
    else:
        evaluate(args.inputs, args.output, args.labels)
