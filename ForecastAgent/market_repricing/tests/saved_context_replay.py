"""Replay exact production reading/handoff failures without provider requests."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path


def run(root, engine, output):
    from ForecastAgent.market_repricing.runtime import activate
    activate(engine)
    from ForecastAgent.market_repricing import SUPER
    os.environ['FORECAST_MODEL'] = SUPER
    os.environ['FORECAST_MODEL_FALLBACK_SUPER'] = '0'
    from ForecastAgent.market_repricing.common import digest, read, write
    from ForecastAgent.market_repricing.context_delivery import adapter
    from ForecastAgent.market_repricing.budget_runtime import native_accounting
    from ForecastAgent.runtime.context import encode, ContextProjectionError
    from ForecastAgent.runtime.retrieval import RetrievalTask
    from ForecastAgent.research_loop import delivery
    from ForecastAgent.releases import v1_0_5 as release
    root = Path(root).resolve()
    keys = ['polymarket:5676709', 'kalshi:KXBRONSONDROPOUT-26-YES', 'kalshi:KXTAYLORDROPOUT-26-YES']
    # Hash original evidence and journals independently of any mutable view.
    originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for key in keys
        for p in (root / 'tasks' / digest(key)).rglob('*') if p.is_file()}
    rows = []
    for key in keys:
        folder = root / 'tasks' / digest(key)
        channel = folder / 'development-intelligence/retrieval/development-channels'
        directory = channel / 'collection'
        raw = read(directory / 'bundle.json')
        case = {'key': key, 'raw_bundle_sha256': originals[directory / 'bundle.json'], 'phases': []}
        baseline_failures = 0
        for forced in (None, 'inspect_research_state', 'update_research_state', 'read_sources', 'search_exa'):
            task = RetrievalTask(directory, raw['request'])
            before = copy.deepcopy(task.bundle)
            error = None
            try: delivery.context(task, forced=forced)
            except ContextProjectionError as exc:
                baseline_failures += 1
                error = str(exc)
            task.bundle = before
            with adapter(folder): messages = delivery.context(task, forced=forced)
            value = json.loads(messages[1]['content'])
            for field in ('question', 'resolution_criteria', 'fine_print', 'background'):
                if field in raw['request']:
                    assert value['immutable_question'][field] == raw['request'][field]
            catalog = __import__('ForecastAgent.research_loop.state', fromlist=['catalog']).catalog(task.bundle, task.cutoff)
            frame = value.get('research_binding_frame') or {}
            for span in frame.get('inspected_evidence', []) + frame.get('source_context', []):
                actual = catalog['spans'][span['evidence_id']]
                for field in ('text', 'url', 'body_sha256', 'start', 'end'):
                    assert span[field] == actual[field]
            assert len(encode(messages)) <= 28000
            case['phases'].append({'forced': forced, 'baseline_error': error, 'new_chars': len(encode(messages)),
                'delivered_evidence_ids': (value.get('delivery_selection') or {}).get('delivered_evidence_ids', []),
                'omitted_evidence_ids': (value.get('delivery_selection') or {}).get('omitted_evidence_ids', []),
                'original_rules_unchanged': True, 'bound_original_spans_unchanged': True})
        assert baseline_failures
        assert not release.raw_handoff_eligible(raw)
        with native_accounting(folder):
            assert release.raw_handoff_eligible(raw)
            assert release.handoff(channel)
        receipt = read(channel / 'raw-handoff.json')
        assert receipt['original_result'] == raw['result']
        case['raw_handoff'] = {'accepted': True, 'new_collection_calls': receipt['new_collection_calls'],
                              'new_search_calls': receipt['new_search_calls'], 'semantic_complete': False,
                              'original_result_preserved': True}
        rows.append(case)
    allowed = {root / 'tasks' / digest(key) / 'development-intelligence/retrieval/development-channels/state.json'
               for key in keys}
    for path, expected in originals.items():
        if path not in allowed: assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, str(path)
    report = {'schema': 'market-context-saved-replay-v1', 'cases': rows,
        'original_files_unchanged': len(originals) - len(allowed), 'allowed_stage_transitions': len(allowed),
        'original_bodies_and_provider_journals_unchanged': True,
        'provider_calls': 0, 'search_calls': 0, 'quota_reset': False, 'real_forecasts_completed': False}
    if (root / 'state.json').exists():
        from ForecastAgent.market_repricing.budgets import recover_context_waits, executions_used
        ledger = read(root / 'state.json')
        before = copy.deepcopy(ledger)
        recovered = recover_context_waits(root, ledger)
        assert set(recovered) == set(keys)
        assert ledger.get('provider_reservations') == before.get('provider_reservations')
        for key, task in before['tasks'].items():
            if key not in keys: assert ledger['tasks'][key] == task
            else: assert ledger['tasks'][key]['executions'] == task['executions']
        report['queue_recovery'] = {'recovered': recovered, 'physical_reservations_unchanged': True,
            'all_other_tasks_unchanged': True, 'original_execution_totals_unchanged': True,
            'service_executions_used': {k: executions_used(ledger['tasks'][k]) for k in keys}}
    write(output, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.engine, args.output)))
