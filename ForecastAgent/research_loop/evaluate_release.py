"""Read-only outcome evaluation of sealed full-release original/map pairs.

No network or model calls. Old receipts without client clocks stay diagnostic;
the question's as_of, an archive mtime and a scheduled resolution are not clocks.
"""
import argparse
import copy
from pathlib import Path

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.providers.decisions import ENDPOINT, MODEL, validate
from ForecastAgent.research_loop.evaluate_shadow import instant, sha, temporal
from ForecastAgent.research_loop.evaluate_trial import score
from ForecastAgent.research_loop.labels import contract, validate as validate_label
from ForecastAgent.research_loop.release_paired_trial import decode, registry
from ForecastAgent.runtime.records import load, save

SCHEMA = 'sealed-release-pair-evaluation-v1'


def inventory(root):
    # Task locks are transient infrastructure, not experimental evidence.
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.name not in {'.running.lock', '.lock.recovery'}}


def closed(root):
    end = load(root/'closure.json')
    if end.get('status') != 'finished' or end.get('restarts_allowed') is not False:
        raise ValueError('Only finished immutable experiments can be sealed')


def seal(root, parent, output, official_status=None):
    """Seal external metadata once; never write into the original experiment."""
    root, parent, output = Path(root).resolve(), Path(parent).resolve(), Path(output).resolve()
    if output.is_relative_to(root) or output == parent or output.exists():
        raise ValueError('Seal must be new and outside the frozen experiment and parent')
    closed(root)
    identity = load(root/'identity.json')
    if sha(parent) != identity['parent_sha256']:
        raise ValueError('Parent differs from registered experiment')
    if not (identity.get('protocol') == 'release-schedule-map-ablation-v1' or
            identity.get('schema') == 'compact_scope_saved_map_score_v1'):
        raise ValueError('Unsupported release pair identity')
    artifact = {'schema': SCHEMA, 'root': str(root), 'parent_path': str(parent),
                'parent_sha256': sha(parent), 'files_sha256': inventory(root),
                'question_contract_sha256': digest(contract(load(parent)['request'])),
                'official_status': None,
                'scope': 'Post-run integrity seal, not preregistration or timestamp attestation.'}
    if official_status:
        path = Path(official_status).resolve()
        artifact['official_status'] = {'path': str(path), 'sha256': sha(path)}
    artifact['seal_sha256'] = digest(artifact)
    save(output, artifact)
    return artifact


def read_arm(folder, question, heads, spec):
    receipts = list((folder/'http').glob('*.json'))
    if len(receipts) != 1:
        raise ValueError('Independent stage requires exactly one physical receipt')
    record = load(receipts[0]); request = load(folder/'request.json')
    if (record.get('status') != 'received' or record.get('http_status') != 200 or
            record.get('endpoint') != ENDPOINT):
        raise ValueError('Stage did not receive a successful HTTP reply')
    if record['request'] != request or request['model'] != MODEL or request['questions'] != heads:
        raise ValueError('Stage receipt, request or full decision registry changed')
    if load(folder/'identity.json')['request_sha256'] != digest(request):
        raise ValueError('Frozen stage request identity changed')
    response = validate(record['response'], heads)
    if response != load(folder/'response.json'):
        raise ValueError('Cached response differs from the physical receipt')
    # Preserve and check the raw wire reply as well as its parsed representation.
    if 'response_body' in record:
        import json
        if json.loads(record['response_body']) != response:
            raise ValueError('Raw response body differs from parsed reply')
    result = {'payload': decode(response, question, spec),
              'receipt_sha256': sha(receipts[0]), 'request_sha256': digest(request),
              'state': request['state'], 'clock_status': 'missing_actual_call_clock',
              'forecast_time': None}
    path = folder/'client-clock.json'
    if path.exists():
        clock = load(path)
        if (clock.get('schema') != 'research-stage-client-clock-v1' or
                clock.get('receipt_status') != 'received' or
                clock.get('clock_source') != 'local_utc_client' or
                clock.get('receipt_sha256') != sha(receipts[0]) or
                clock.get('request_sha256') != digest(request) or
                clock.get('response_sha256') != digest(response)):
            raise ValueError('Actual call clock is not bound to this received reply')
        if instant(clock['finished_at_utc']) < instant(clock['started_at_utc']):
            raise ValueError('Actual call clock runs backwards')
        result.update(clock_status='receipt_bound_local_client_clock',
                      forecast_time=clock['finished_at_utc'], clock_sha256=sha(path))
    return result


def saved_originals(state, bundle):
    # The frozen release packet removes this redundant platform grid array.
    # All other question fields must match literally; no semantic normalization.
    expected = copy.deepcopy(bundle['request'])
    if isinstance(expected.get('scaling'), dict):
        expected['scaling'].pop('continuous_range', None)
    if state['question'] != expected or state.get('research_map'):
        raise ValueError('Original arm changes question contract or contains a map')
    sources = {s['source_id']: s for s in state['sources']}
    if len(sources) != len(state['sources']):
        raise ValueError('Duplicate source IDs')
    ids = set()
    for span in state['evidence']:
        if span['evidence_id'] in ids:
            raise ValueError('Duplicate evidence IDs')
        ids.add(span['evidence_id'])
        source = sources[span['source_id']]
        body = bundle['pages'][source['url']]['content']
        import hashlib
        if hashlib.sha256(body.encode()).hexdigest() != source['body_sha256']:
            raise ValueError('Selected original source hash changed')
        start, end = span['start'], span['end']
        if (isinstance(start, bool) or isinstance(end, bool) or
                not isinstance(start, int) or not isinstance(end, int) or
                not 0 <= start < end <= len(body) or body[start:end] != span['text']):
            raise ValueError('Selected original does not match its source coordinates')


def evaluate(seal_path, binding=None):
    frozen = load(seal_path); row = {'evaluation_status': 'pending_outcome', 'metrics': None,
                                  'independent_pair': False, 'prospective_pair_eligible': False}
    result = {'schema': SCHEMA, 'seal_file_sha256': sha(seal_path), 'row': row,
              'new_http': 0, 'submitted': False, 'full_production_worker_comparison': False,
              'limitation': 'Post-run seal verifies preserved records, not source truth, clock attestation, model-memory cleanliness or untouched predictive performance.'}
    try:
        if frozen['schema'] != SCHEMA or frozen['seal_sha256'] != digest(
                {k: v for k, v in frozen.items() if k != 'seal_sha256'}):
            raise ValueError('Evaluation seal checksum changed')
        root = Path(frozen['root']); parent = Path(frozen['parent_path']); closed(root)
        if inventory(root) != frozen['files_sha256'] or sha(parent) != frozen['parent_sha256']:
            raise ValueError('Sealed experiment files or parent changed')
        bundle = load(parent); question = bundle['request']; identity = load(root/'identity.json')
        if (identity['parent_sha256'] != frozen['parent_sha256'] or
                digest(contract(question)) != frozen['question_contract_sha256']):
            raise ValueError('Frozen question or registered parent identity changed')
        row.update(id=str(question['id']), kind=question['question_type'],
                   declared_as_of=question.get('as_of_utc'), label_binding=validate_label(question, binding))
        heads, spec = registry(question); paired = load(root/'result.json')
        stages = paired['stages']
        names = [s['stage'] for s in stages]
        if names not in (['first'], ['first', 'second']) or paired['status'] != 'completed':
            raise ValueError('Invalid or incomplete release stage sequence')
        if paired['routing_owner'] != 'original_release_arm' or paired['mapped_own_routing_applied'] is not False:
            raise ValueError('Unexpected owner of release routing')
        if paired['second_call_required'] != (len(stages) == 2):
            raise ValueError('Final reread flag disagrees with actual stage sequence')
        first = load(root/'common-first.json')
        if identity.get('first_original_sha256', digest(first)) != digest(first):
            raise ValueError('Frozen first reading changed')
        audits = []
        for stage in stages:
            name = stage['stage']; folder = root/'analysis'
            a = read_arm(folder/'original'/name, question, heads, spec)
            saved_originals(a['state'], bundle)
            if (a['state'] != stage['originals'] or digest(a['state']) != stage['original_state_sha256'] or
                    digest(heads) != stage['registry_sha256'] or a['payload'] != stage['original_payload']):
                raise ValueError('Preserved original stage differs from independently decoded reply')
            if name == 'first' and a['state'] != first:
                raise ValueError('Actual first original differs from its frozen input')
            if stage.get('independent_map_request'):
                b = read_arm(folder/'mapped'/name, question, heads, spec)
                if (not b['state'].get('research_map', {}).get('factual_grounding_present') or
                        {k: v for k, v in b['state'].items() if k != 'research_map'} != a['state']):
                    raise ValueError('Mapped arm does not share exact originals and scope qualifiers')
                if b['payload'] != stage['mapped_payload'] or stage.get('mapped_error'):
                    raise ValueError('Mapped stage differs from independently decoded reply')
            else:
                b = None
                paths = list((folder/'mapped'/name/'http').glob('*.json'))
                # A failed B is retained. A successful hidden B cannot be silently
                # presented as a fallback or an absent experiment.
                if any(load(p).get('status') == 'received' for p in paths):
                    raise ValueError('Non-independent stage has a hidden successful mapped reply')
                if not stage.get('mapped_error') and stage['mapped_payload'] != a['payload']:
                    raise ValueError('Fallback is not the original reply')
            audits.append({'stage': name, 'original': {k: v for k, v in a.items() if k != 'state'},
                           'mapped': {k: v for k, v in b.items() if k != 'state'} if b else None,
                           'independent': b is not None})
        final = audits[-1]
        hidden_failures = []
        for arm in ('original', 'mapped'):
            for path in (root/'analysis'/arm).glob('*/http/*.json'):
                name = path.parent.parent.name
                if name not in names:
                    record = load(path)
                    if name not in {'first', 'second'} or record.get('status') == 'received':
                        raise ValueError('Unreported successful or unexpected decision stage')
                    hidden_failures.append({'arm': arm, 'stage': name, 'status': record.get('status'),
                                            'receipt_sha256': sha(path)})
        if (paired['original'] != final['original']['payload'] or
                paired['independent_final_pair'] != final['independent'] or
                paired['mapped'] != stages[-1]['mapped_payload']):
            raise ValueError('Final result is not the final received release stage')
        row.update(stages=audits, independent_pair=final['independent'],
                   original=final['original']['payload'], mapped=paired['mapped'],
                   scope=paired['scope'], failed_uncompleted_stages=hidden_failures,
                   label_binding=validate_label(question, binding))
        clock_question = copy.deepcopy(question)
        if frozen.get('official_status'):
            reference = frozen['official_status']; path = Path(reference['path'])
            if sha(path) != reference['sha256']:
                raise ValueError('Archived official status changed')
            status = load(path)
            if str(status['id']) != str(question['id']) or str(status['post_id']) != str(question['post_id']):
                raise ValueError('Official status belongs to another question')
            clock_question['close_time'] = status.get('actual_close_time') or status.get('scheduled_close_time')
            clock_question.pop('scheduled_close_time', None)
        row['temporal'] = {}
        for arm in ('original', 'mapped'):
            received = final[arm]
            if received is None:
                continue
            clock = received['forecast_time']
            audit = temporal(clock, clock_question, binding) if clock else {
                'status': 'missing_actual_call_clock', 'prospective_eligible': False,
                'official_deadline_eligible': False}
            audit['declared_as_of_not_used'] = True
            row['temporal'][arm] = audit
        row['prospective_pair_eligible'] = final['independent'] and all(
            row['temporal'][arm]['prospective_eligible'] for arm in ('original', 'mapped'))
        if not final['independent']:
            row['evaluation_status'] = 'not_independent'
        elif binding is None:
            row['evaluation_status'] = 'pending_outcome'
        elif not row['label_binding']['eligible']:
            row['evaluation_status'] = 'quarantined_label'
        else:
            row['metrics'] = {arm: score(row[arm], question, binding['label']) for arm in ('original', 'mapped')}
            row['difference_B_minus_A'] = {k: row['metrics']['mapped'][k]-v for k, v in row['metrics']['original'].items()}
            row['evaluation_status'] = ('evaluated_prospective_diagnostic' if row['prospective_pair_eligible']
                                        else 'evaluated_historical_diagnostic')
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        row.update(evaluation_status='quarantined_pair', error=str(exc), metrics=None,
                   independent_pair=False, prospective_pair_eligible=False)
        row.pop('difference_B_minus_A', None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('seal')
    create.add_argument('--root', type=Path, required=True)
    create.add_argument('--parent', type=Path, required=True)
    create.add_argument('--official-status', type=Path)
    create.add_argument('--output', type=Path, required=True)
    run = commands.add_parser('evaluate')
    run.add_argument('--seal', type=Path, required=True)
    run.add_argument('--binding', type=Path)
    run.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'seal':
        seal(args.root, args.parent, args.output, args.official_status)
    else:
        frozen = load(args.seal)
        output = args.output.resolve()
        if (output.is_relative_to(Path(frozen['root']).resolve()) or output == args.seal.resolve()
                or output == Path(frozen['parent_path']).resolve() or
                (args.binding and output == args.binding.resolve())):
            raise ValueError('Evaluation cannot overwrite any frozen input')
        save(output, evaluate(args.seal, load(args.binding) if args.binding else None))


if __name__ == '__main__':
    main()
