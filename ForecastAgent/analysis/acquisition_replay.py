"""Score immutable unified acquisition packages; labels are evaluation-only."""
import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.ensemble import metrics
from ForecastAgent.analysis.pilot import WARNING, digest, load, save

PROTOCOL = 'unified-acquisition-mercury-replay-v1'
QUESTION_FIELDS = ('id', 'question', 'resolution_criteria', 'fine_print', 'background',
                   'open_time', 'market_info_open_datetime', 'forecast_due_date',
                   'close_time', 'scheduled_close_time', 'scheduled_resolve_time')


def visible_bundle(bundle):
    """Exclude acquisition opinions, review outputs, labels and market prices."""
    result = {k: copy.deepcopy(bundle[k]) for k in
              ('pages', 'gaps', 'collection_handoff', 'source_aliases', 'supplement_lineage') if k in bundle}
    result['request'] = {k: copy.deepcopy(bundle['request'][k]) for k in QUESTION_FIELDS if k in bundle['request']}
    return result


def run(inputs, output, manifest, *, dry_run=False):
    inputs, output = Path(inputs), Path(output)
    frozen = load(manifest)
    cases = frozen['cases']
    if not 1 <= len(cases) <= 5 or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('One to five unique frozen questions required')
    if frozen['chain_sha256'] != hashlib.sha256(Path(chain.__file__).read_bytes()).hexdigest():
        raise ValueError('Frozen analysis implementation changed')
    prepared = []
    # Validate the entire batch before reserving any requests.
    for case in cases:
        ident = case['id']
        if not ident.isdigit():
            raise ValueError('Numeric platform question ID required')
        bundle = load(inputs/'tasks'/ident/'analysis-input.json')
        package = load(inputs/'tasks'/ident/'package.json')
        if digest(bundle) != case['bundle_sha256'] or package['analysis_input_sha256'] != digest(bundle):
            raise ValueError('Frozen acquisition package changed: ' + ident)
        if str(bundle['request']['id']) != ident:
            raise ValueError('Question identity mismatch')
        prepared.append((case, visible_bundle(bundle)))
    identity = {'protocol': PROTOCOL, 'manifest': frozen, 'dry_run': dry_run,
                'bridge_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'outcome_labels_loaded': False, 'new_retrieval_calls': 0, 'forecast_submissions': 0}
    if (output/'identity.json').exists() and load(output/'identity.json') != identity:
        raise ValueError('Frozen replay identity changed')
    save(output/'identity.json', identity)
    rows = []
    for case, bundle in prepared:
        ident = case['id']; task = output/'tasks'/ident
        save(task/'analysis-input.json', bundle)
        row = {'id': ident, 'status': 'failed', 'acquisition_bundle_sha256': case['bundle_sha256'],
               'visible_bundle_sha256': digest(bundle), 'saved_pages': len(bundle.get('pages', {})),
               'source_gaps': bundle.get('gaps', []), 'source_run': frozen['source_run']}
        metadata = {k: bundle['request'][k] for k in QUESTION_FIELDS if k in bundle['request']}
        try:
            row.update(chain.run_task(bundle, task/'mercury', metadata, dry_run))
        except Exception as exc:
            row['error'] = str(exc)
            # Same retained-first policy as the existing fifty-question analysis.
            if isinstance(exc, RuntimeError) and (task/'mercury/first/response.json').exists() and not (task/'mercury/second/response.json').exists():
                response = chain.decisions.validate(load(task/'mercury/first/response.json'), chain.questions())
                p = response['answers']['event_yes']['noul']
                row.update(status='completed_first_retained', probability_yes=p,
                           clipped_probability_yes=min(.98, max(.02, p)), second_error=str(exc))
        save(task/'outcome.json', row); rows.append(row)
        save(output/'report.json', {'protocol': PROTOCOL, 'rows': rows, 'evaluation_warning': WARNING,
             'outcome_labels_loaded': False, 'new_retrieval_calls': 0, 'forecast_submissions': 0})
        print(json.dumps({'id': ident, 'status': row['status']}), flush=True)
    return rows


def evaluate(results, labels, output):
    """Audit exact inputs, source offsets and responses before joining outcomes."""
    results, output = Path(results), Path(output)
    identity = load(results/'identity.json')
    frozen = []; usage = []
    for case in identity['manifest']['cases']:
        task = results/'tasks'/case['id']; row = load(task/'outcome.json')
        bundle = load(task/'analysis-input.json')
        if row['visible_bundle_sha256'] != digest(bundle):
            raise ValueError('Visible evidence changed')
        folder = task/'mercury'
        for stage in ('first', 'second'):
            path = folder/(stage+'-state.json')
            if not path.exists():
                continue
            state = load(path)
            request_path = folder/stage/'request.json'
            if request_path.exists() and load(request_path)['state'] != state:
                raise ValueError('Provider request state changed')
            if chain.request_bytes(state) > (chain.FIRST_BYTES if stage == 'first' else chain.SECOND_BYTES):
                raise ValueError('Request byte bound exceeded')
            for span in state['evidence']:
                body = bundle['pages'][span['url']]['content']
                if hashlib.sha256(body.encode()).hexdigest() != span['body_sha256'] or body[span['start']:span['end']] != span['text']:
                    raise ValueError('Original evidence binding changed')
        if 'probability_yes' in row:
            stage = 'second' if row['status'] == 'completed' and row.get('second_call_required') else 'first'
            response = chain.decisions.validate(load(folder/stage/'response.json'), chain.questions())
            if row['probability_yes'] != response['answers']['event_yes']['noul'] or row['clipped_probability_yes'] != min(.98, max(.02, row['probability_yes'])):
                raise ValueError('Frozen probability changed')
        attempts = list(folder.glob('*/http/*.json'))
        if len(attempts) > 2:
            raise ValueError('Lifetime analysis attempt cap exceeded')
        for path in sorted(attempts):
            record = load(path); data = record.get('response', {}).get('usage', {}) or {}
            total = data.get('total_tokens')
            if total is None and isinstance(data.get('input_tokens'), int) and isinstance(data.get('output_tokens'), int):
                total = data['input_tokens'] + data['output_tokens']
            usage.append({'id': row['id'], 'status': record['status'], 'http_status': record.get('http_status'),
                          'tokens': total, 'input_tokens': data.get('input_tokens'),
                          'output_tokens': data.get('output_tokens'), 'reported_cost_usd': data.get('cost')})
        frozen.append(row)
    predictions_hash = digest(frozen)
    outcomes = load(labels)
    lookup = {str(r['id']): r['resolved_to'] for r in outcomes['labels']}
    scored = [{**r, 'resolution': lookup[r['id']], **metrics(r['clipped_probability_yes'], lookup[r['id']])}
              for r in frozen if 'probability_yes' in r]
    def aggregate(rows):
        return {'n': len(rows), **({k: sum(r[k] for r in rows)/len(rows)
                for k in ('brier', 'log_loss', 'correct_at_half')} if rows else {})}
    report = {'protocol': PROTOCOL, 'requested': len(frozen), 'scored': len(scored),
              'metrics': aggregate(scored), 'by_outcome': {str(y): aggregate([r for r in scored if r['resolution'] == y]) for y in (0, 1)},
              'state_distribution': dict(Counter(r['status'] for r in frozen)),
              'http_attempts': len(usage), 'known_tokens': sum(r['tokens'] for r in usage if isinstance(r['tokens'], int)),
              'unknown_usage_attempts': sum(r['tokens'] is None for r in usage),
              'frozen_predictions_sha256': predictions_hash, 'label_provenance': outcomes['provenance'],
              'labels_sha256': digest(outcomes), 'results': scored,
              'failures': [r for r in frozen if 'probability_yes' not in r], 'usage': usage,
              'neutral_half_brier': .25, 'evaluation_warning': WARNING,
              'new_retrieval_calls': 0, 'forecast_submissions': 0}
    save(output/'metrics.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'evaluate'])
    parser.add_argument('--inputs', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--manifest'); parser.add_argument('--labels'); parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.action == 'run':
        rows = run(args.inputs, args.output, args.manifest, dry_run=args.dry_run)
        if any(r['status'] == 'failed' for r in rows):
            raise SystemExit(1)
    else:
        print(json.dumps(evaluate(args.inputs, args.labels, args.output)['metrics'], indent=2))
