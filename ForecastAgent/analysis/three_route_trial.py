"""Frozen unseen cohort: existing evidence to reasoning, Mercury and Jev."""
import argparse
from pathlib import Path

from ForecastAgent.analysis import referenced, jev_comparison
from ForecastAgent.competition.queue import load, save, digest


def run(inputs, supplements, output, batch, *, cohort_path=None, audit_contract=False, metadata_path=None):
    cohort = load(cohort_path or Path(__file__).with_name('three_route_cohort.json'))
    ids = cohort['batches'][batch - 1]
    output = Path(output)
    frozen = output/'selection.json'
    selection = {'cohort': cohort, 'batch': batch, 'ids': ids}
    if frozen.exists() and load(frozen) != selection:
        raise ValueError('Frozen batch identity changed')
    save(frozen, selection)
    name = f'batch-{batch}'
    analysis = output/'analysis'/name
    errors = []
    try:
        referenced.run(inputs, analysis, ids, supplements, mode='both',
                       audit_contract=audit_contract,
                       question_metadata=load(metadata_path) if metadata_path else None)
    except Exception as exc:
        errors.append({'stage': 'reasoning_and_mercury', 'error': type(exc).__name__+': '+str(exc)})
    # Evaluation labels are loaded after reasoning; never placed in model state.
    labels = load(Path(__file__).with_name('three_route_labels.json'))
    rows, missing = [], []
    for ident in ids:
        path = analysis/'tasks'/ident/'result.json'
        result = load(path) if path.exists() else {}
        if result.get('reasoning_probability_yes') is None or result.get('mercury_probability_yes') is None:
            missing.append({'id': ident, 'result': result})
            continue
        rows.append({'id': ident, 'run_id': name, 'reasoning_p': result['reasoning_probability_yes'],
            'mercury_p': result['mercury_probability_yes'], 'resolution': labels[ident]})
    comparison_cohort = output/'jev-cohort.json'
    save(comparison_cohort, {'schema': 'jev-new20-batch-v1', 'rows': rows,
        'evaluation_warning': ['Fixed unseen topic/gap-stratified historical sample.',
            'Saved evidence may contain outcomes; this is a retrospective diagnostic.',
            'Jev and Mercury consume shared qualitative analysis; the routes are not independent.']})
    if rows:
        jev_comparison.run(output/'analysis', output/'jev', comparison_cohort)
    save(output/'batch-report.json', {'schema': 'three-route-new20-batch-v1', 'batch': batch,
        'requested_ids': ids, 'selection_sha256': digest(cohort), 'errors': errors,
        'missing_baseline_routes': missing,
        'jev_report': load(output/'jev'/'report.json') if (output/'jev'/'report.json').exists() else None,
        'no_retrieval_calls': True, 'no_supplement_calls': True, 'no_forecasts_submitted': True})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--supplements', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--batch', type=int, choices=range(1,5), required=True)
    parser.add_argument('--cohort')
    parser.add_argument('--audit-contract', action='store_true')
    parser.add_argument('--metadata')
    args = parser.parse_args()
    run(args.inputs, args.supplements, args.output, args.batch,
        cohort_path=args.cohort, audit_contract=args.audit_contract, metadata_path=args.metadata)
