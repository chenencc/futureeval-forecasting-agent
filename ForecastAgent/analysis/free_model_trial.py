"""Model-only comparison using the frozen seven-question repair contract."""
import argparse
import hashlib
from pathlib import Path

from ForecastAgent.analysis import referenced
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.providers.model import configured_model

MODELS = {
    'qwen': 'qwen/qwen3.8-27b:free',
    'apodex': 'apodex/apodex-1.1-mini:free',
    'inkling': 'thinkingmachines/inkling:free',
}


def run(inputs, supplements, output, batch, model):
    if configured_model() != MODELS[model]:
        raise ValueError('Experiment model does not match the selected backend')
    root = Path(__file__).parent
    cohort = load(root / 'error_seven_cohort.json')
    ids = cohort['batches'][batch - 1]
    output = Path(output)
    identity = {'schema': 'free-model-seven-v1', 'model': MODELS[model],
                'batch': batch, 'ids': ids, 'baseline_run': 37005403600,
                'analysis_source_sha256': hashlib.sha256(Path(referenced.__file__).read_bytes()).hexdigest(),
                'fallback_enabled': False, 'http_cap_per_question': 3,
                'generation': referenced.GENERATION}
    selection = output / 'selection.json'
    if selection.exists() and load(selection) != identity:
        raise ValueError('Frozen experiment identity changed')
    save(selection, identity)
    error = None
    try:
        referenced.run(inputs, output / 'analysis', ids, supplements,
                       mode='reasoning_only', audit_contract=True,
                       question_metadata=load(root / 'error_seven_time_metadata.json'))
    except Exception as exc:
        error = type(exc).__name__ + ': ' + str(exc)
    # Labels are loaded only after analysis. They never enter the model conversation.
    labels = load(root / 'three_route_labels.json')
    rows = []
    for ident in ids:
        folder = output / 'analysis' / 'tasks' / ident
        result = load(folder / 'result.json') if (folder / 'result.json').exists() else {}
        session = load(folder / 'session.json') if (folder / 'session.json').exists() else {}
        rows.append({'id': ident, 'resolution': labels[ident], 'result': result,
                     'actual_model': session.get('draft_model'),
                     'reviewed': session.get('reviewed', False),
                     'local_read_calls': session.get('local_read_calls', 0)})
    save(output / 'batch-report.json', {'identity': identity, 'error': error, 'rows': rows,
         'warnings': ['Known-error development cohort, not a held-out evaluation.',
                      'Historical saved evidence may disclose outcomes.'],
         'no_retrieval_calls': True, 'no_decision_model_calls': True,
         'no_forecasts_submitted': True})
    if error:
        raise RuntimeError(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--supplements', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--batch', type=int, choices=[1, 2], required=True)
    parser.add_argument('--model', choices=list(MODELS), required=True)
    args = parser.parse_args()
    run(args.inputs, args.supplements, args.output, args.batch, args.model)
