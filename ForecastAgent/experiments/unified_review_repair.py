"""Bounded identical-state selection transport repair for a completed pilot.

This does not change V7 semantics or reading coverage, reopen acquisition, or
reset any allowance. Each independent choice head uses its original full state.
"""
import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.providers import decisions
from ForecastAgent.supplement import mercury_material_v7 as v7, mercury_wire as wire
from ForecastAgent.runtime.task_lock import task_lock


def run(parent, output, key):
    parent, output = Path(parent), Path(output)
    if output.exists():
        raise ValueError('existing_repair_cannot_reset_reservations')
    shutil.copytree(parent, output)
    with task_lock(output):
        root_state = load(output / 'state.json')
        folder = output / root_state['review_directory']
        state = load(folder / 'state.json')
        manifest = load(output / 'manifest.json')
        for filename, expected in manifest.get('review_code_sha256', {}).items():
            if hashlib.sha256((Path(__file__).resolve().parents[2] / filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
                raise ValueError('frozen_v7_core_changed')
        old_elapsed = state['elapsed_seconds']
        started = time.monotonic()
        active = {}
        def persist():
            state['elapsed_seconds'] = old_elapsed + time.monotonic() - started
            save(folder / 'state.json', state)
        def observer(event, record, token=None):
            if event == 'reserve':
                if len(state['attempts']) >= state['limits']['http']:
                    raise RuntimeError('preserved_http_cap_exhausted')
                token = len(state['attempts'])
                state['attempts'].append({**active, 'status': 'reserved'})
                persist()
            filename = f'provider-{token+1:04d}.json'
            save(folder / filename, json.loads(json.dumps(record).replace(key, '[REDACTED]')))
            state['attempts'][token].update(status=record['status'], file=filename,
                usage=(record.get('response') or {}).get('usage'), http_status=record.get('http_status'))
            persist()
            return token
        def execute(phase, prepared, *, split=False):
            projected = wire.prepare(prepared)
            if len(state['calls']) >= state['limits']['logical'] or state['elapsed_seconds'] >= state['limits']['seconds']:
                raise RuntimeError('preserved_logical_or_time_cap_exhausted')
            if len(json.dumps({'state': projected['state'], 'questions': projected['questions']}).encode()) > state['limits']['request_bytes']:
                raise ValueError('preserved_request_byte_cap_exhausted')
            active.update(phase=phase, prepared_file=f'request-{len(state["calls"])+1:04d}.json')
            save(folder / active['prepared_file'], projected)
            call = {**active, 'status': 'reserved', 'split_independent_heads': split}
            state['calls'].append(call); persist()
            try:
                if not split:
                    response = decisions.decide(projected['state'], projected['questions'], key, observer)
                else:
                    response = {'model': decisions.MODEL, 'answers': {}}
                    for name, question in projected['questions'].items():
                        active['phase'] = phase + '_head'
                        active['question_key'] = name
                        partial = decisions.decide(projected['state'], {name: question}, key, observer)
                        response['answers'][name] = partial['answers'][name]
                    decisions.validate(response, projected['questions'])
                    active.pop('question_key', None)
                    active['phase'] = phase
                    save(folder / (active['case_id'] + '-v7-selection-merged-response.json'), response)
                call['status'] = 'received'
                return response
            except Exception as exc:
                call.update(status='failed', error=str(exc)[:300])
                raise
            finally:
                persist()
        repaired = []
        for case in manifest['cases']:
            cid = case['id']
            row = next(r for r in state['cases'] if r['case_id'] == cid)
            if row['pair_complete']:
                continue
            attempts = [a for a in state['attempts'] if a['case_id'] == cid and a['phase'] == 'selection']
            if len(attempts) < 2 or not all(a.get('http_status') == 422 for a in attempts):
                raise ValueError('repair_requires_two_recorded_selection_422_failures')
            active.update(case_id=cid, arm='v7')
            bundle = load(output / 'tasks' / str(case['question_id']) / 'analysis-input.json')
            package = load(output / 'tasks' / str(case['question_id']) / 'package.json')
            if digest(bundle) != package['analysis_input_sha256']:
                raise ValueError('frozen_body_changed')
            try:
                p = v7.prepare_selection(bundle, case['plan'])
                if p != load(folder / (cid + '-v7-selection-prepared.json')):
                    raise ValueError('selection_material_or_semantics_changed')
                selected = v7.bind_selection(p, execute('selection', p, split=True), bundle)
                save(folder / (cid + '-v7-selection.json'), selected)
                prepared = v7.prepare_assessment(bundle, case['plan'], selected)
                save(folder / (cid + '-v7-prepared.json'), prepared)
                result = v7.bind_assessment(prepared, execute('assessment', prepared), bundle)
                save(folder / (cid + '-v7-initial-result.json'), result)
                guard = v7.prepare_guard(prepared, result)
                if guard['questions']:
                    save(folder / (cid + '-v7-guard-prepared.json'), guard)
                    reply = execute('scope_guard', guard)
                    save(folder / (cid + '-v7-guard-response.json'), reply)
                    result = v7.bind_guard(result, guard, reply)
                name = cid + '-v7-result.json'; save(folder / name, result)
                row['arms']['v7'] = {'status': 'received', 'file': name, 'application_status': result['application_status']}
                row['pair_complete'] = True
                repaired.append(cid)
            except Exception as exc:
                row['arms']['v7'] = {'status': 'failed', 'error': str(exc)[:300]}
            persist()
        review_result = {**state, 'actual_http_attempts': len(state['attempts']), 'logical_decisions': len(state['calls']),
            'business_gate_passed': all(r['pair_complete'] for r in state['cases']),
            'pending_case_ids': [r['case_id'] for r in state['cases'] if not r['pair_complete']],
            'transport_repair': {'protocol': 'independent-choice-heads-v1', 'repaired_cases': repaired,
                'same_full_state': True, 'semantic_core_changed': False, 'budgets_reset': False,
                'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest()},
            'forecast_submissions': 0}
        save(folder / 'result.json', review_result)
        for row in root_state['cases']:
            reviewed = next(r for r in state['cases'] if r['case_id'] == row['id'])
            row.update(stage='complete' if reviewed['pair_complete'] else 'review_failed', review=reviewed['arms']['v7'])
        root_state.update(complete=review_result['business_gate_passed'], review_summary={
            k:review_result[k] for k in ('actual_http_attempts', 'logical_decisions', 'pending_case_ids')},
            transport_repair=review_result['transport_repair'])
        save(output / 'state.json', root_state); save(output / 'result.json', root_state)
        return root_state


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent', required=True); p.add_argument('--output', required=True)
    a = p.parse_args()
    r = run(a.parent, a.output, os.environ['OPENROUTER_API_KEY'])
    print(json.dumps({'complete': r['complete'], 'transport_repair': r['transport_repair']}))
    if not r['complete']:
        raise SystemExit(1)
