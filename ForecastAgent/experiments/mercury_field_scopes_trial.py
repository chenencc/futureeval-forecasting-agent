"""Bounded V5/V7 independent-field regression; no acquisition or forecasts."""
import argparse
import copy
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.providers import decisions
from ForecastAgent.supplement import mercury_material_v5 as v5
from ForecastAgent.supplement import mercury_material_v7 as v7
from ForecastAgent.supplement import mercury_candidate_groups as groups
from ForecastAgent.supplement import mercury_wire as wire

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name('MERCURY_FIELD_SCOPES5.json')


def run(output, key, manifest=MANIFEST, *, resume=None, max_cases=5):
    data = load(manifest)
    arms = data.get('arms', ['v5', 'v7'])
    if not arms or len(set(arms)) != len(arms) or not set(arms) <= {'v5', 'v7'}:
        raise ValueError('invalid_frozen_arms')
    if not 1 <= max_cases <= 5:
        raise ValueError('batch_must_be_one_to_five')
    if any(set(c['arm_order']) != set(arms) or len(c['arm_order']) != len(arms) for c in data['cases']):
        raise ValueError('case_arms_differ_from_manifest')
    for filename, expected in data['frozen_code_sha256'].items():
        if hashlib.sha256((ROOT / filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise ValueError('frozen_code_changed')
    output = Path(output)
    if output.exists():
        raise ValueError('existing_trial_cannot_reset_budget')
    identity = hashlib.sha256(Path(manifest).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if resume:
        parent = Path(resume)
        if load(parent / 'identity.json')['manifest_sha256'] != identity:
            raise ValueError('wrong_parent_identity')
        state = load(parent / 'state.json')
        if state['limits'] != data['limits'] or state['prior_experiments'] != data['prior_experiments']:
            raise ValueError('resume_cannot_change_limits_or_history')
        shutil.copytree(parent, output)
        state['blocked'] = False
    else:
        output.mkdir(parents=True)
        state = {'trial_id': data['trial_id'], 'limits': data['limits'],
            'prior_experiments': data['prior_experiments'], 'calls': [], 'attempts': [],
            'cases': [], 'blocked': False, 'elapsed_seconds': 0}
    save(output / 'identity.json', {'manifest_sha256': identity,
        'frozen_code_sha256': data['frozen_code_sha256'], 'independent_trial_no_old_quota_reset': True})
    save(output / 'manifest.json', data)
    active, starts = {}, {}
    started = time.monotonic()
    prior_elapsed = state['elapsed_seconds']

    def persist():
        state['elapsed_seconds'] = prior_elapsed + time.monotonic() - started
        save(output / 'state.json', state)

    def observer(event, record, token=None):
        if event == 'reserve':
            if len(state['attempts']) >= data['limits']['http']:
                raise RuntimeError('frozen_http_cap_exhausted')
            if record['request']['model'] != decisions.MODEL:
                raise RuntimeError('unexpected_model')
            token = len(state['attempts'])
            starts[token] = time.monotonic()
            state['attempts'].append({**active, 'status': 'reserved'})
            persist()
        filename = f'provider-{token+1:04d}.json'
        safe = {**record, 'elapsed_seconds': time.monotonic() - starts[token]}
        save(output / filename, json.loads(json.dumps(safe).replace(key, '[REDACTED]')))
        state['attempts'][token] = {**active, 'status': record['status'], 'file': filename,
            'usage': record.get('response', {}).get('usage'), 'http_status': record.get('http_status'),
            'elapsed_seconds': safe['elapsed_seconds']}
        persist()
        return token

    def execute(phase, prepared):
        projected = wire.prepare(prepared)
        if len(state['calls']) >= data['limits']['logical'] or state['elapsed_seconds'] >= data['limits']['seconds']:
            raise RuntimeError('frozen_trial_limit_exhausted')
        if len(json.dumps({'state': projected['state'], 'questions': projected['questions']}).encode()) > data['limits']['request_bytes']:
            raise ValueError('request_byte_guard_exceeded')
        active['phase'] = phase
        active['prepared_file'] = f'request-{len(state["calls"])+1:04d}.json'
        save(output / active['prepared_file'], projected)
        call = {**active, 'status': 'reserved'}
        state['calls'].append(call)
        persist()
        try:
            response = decisions.decide(projected['state'], projected['questions'], key, observer)
            call['status'] = 'received'
            return response
        except Exception as exc:
            call.update(status='failed', error=str(exc)[:240])
            if not isinstance(exc, ValueError) and str(exc) != 'Decision endpoint HTTP 422':
                state['blocked'] = True
            raise
        finally:
            persist()

    persist()
    processed = 0
    for case in data['cases']:
        active.clear()
        active['case_id'] = case['id']
        row = next((r for r in state['cases'] if r['case_id'] == case['id']), None)
        if row and row.get('pair_complete'):
            continue
        if processed >= max_cases:
            break
        processed += 1
        if row is None:
            row = {'case_id': case['id'], 'question_id': case['question_id'], 'arms': {}}
            state['cases'].append(row)
        bundle, plan = case['bundle'], case['plan']
        baseline = v5.v1.prepare(bundle, plan)
        save(output / (case['id'] + '-frozen-input.json'), case)
        row['need_count'] = len(plan['needs'])
        for arm in case['arm_order']:
            if row['arms'].get(arm, {}).get('status') == 'received':
                continue
            active['arm'] = arm
            try:
                if arm == 'v5':
                    contract_file = output / (case['id'] + '-v5-typed-contract.json')
                    if contract_file.exists():
                        contract = load(contract_file)
                    else:
                        p = v5.prepare_contract(bundle, plan)
                        save(output / (case['id'] + '-v5-contract-prepared.json'), p)
                        reply = execute('contract', p)
                        save(output / (case['id'] + '-v5-contract-response.json'), reply)
                        contract = v5.bind_contract(p, reply)
                        save(contract_file, contract)
                    prepared = groups.prepare(v5.prepare(bundle, plan, contract))
                    for field in ('question', 'needs', 'reading', 'rule_catalog'):
                        if prepared['state'][field] != baseline['state'][field]:
                            raise ValueError('paired_input_coverage_changed')
                    save(output / (case['id'] + '-v5-prepared.json'), prepared)
                    initial_file = output / (case['id'] + '-v5-initial-result.json')
                    if initial_file.exists():
                        result = load(initial_file)
                    else:
                        result = v5.bind(prepared, execute('proof', prepared), bundle)
                        save(initial_file, result)
                else:
                    p = v7.prepare_selection(bundle, plan)
                    for field in ('question', 'needs', 'reading', 'rule_catalog'):
                        if p['state'][field] != baseline['state'][field]:
                            raise ValueError('paired_selection_coverage_changed')
                    save(output / (case['id'] + '-v7-selection-prepared.json'), p)
                    selection_file = output / (case['id'] + '-v7-selection.json')
                    if selection_file.exists():
                        selected = load(selection_file)
                    else:
                        selected = v7.bind_selection(p, execute('selection', p), bundle)
                        save(selection_file, selected)
                    prepared = v7.prepare_assessment(bundle, plan, selected)
                    save(output / (case['id'] + '-v7-prepared.json'), prepared)
                    initial_file = output / (case['id'] + '-v7-initial-result.json')
                    if initial_file.exists():
                        result = load(initial_file)
                    else:
                        result = v7.bind_assessment(prepared, execute('assessment', prepared), bundle)
                        save(initial_file, result)
                    guard = v7.prepare_guard(prepared, result)
                    if guard['questions']:
                        save(output / (case['id'] + '-v7-guard-prepared.json'), guard)
                        guard_reply_file = output / (case['id'] + '-v7-guard-response.json')
                        if guard_reply_file.exists():
                            reply = load(guard_reply_file)
                        else:
                            reply = execute('scope_guard', guard)
                            save(guard_reply_file, reply)
                        result = v7.bind_guard(result, guard, reply)
                filename = case['id'] + '-' + arm + '-result.json'
                save(output / filename, result)
                row['arms'][arm] = {'status': 'received', 'file': filename,
                    'application_status': result['application_status']}
            except Exception as exc:
                row['arms'][arm] = {'status': 'failed', 'error': str(exc)[:240]}
                if not isinstance(exc, ValueError) and str(exc) != 'Decision endpoint HTTP 422':
                    state['blocked'] = True
            persist()
            if state['blocked']:
                break
        row['pair_complete'] = set(row['arms']) == set(arms) and all(a.get('status') == 'received' for a in row['arms'].values())
        persist()
        if state['blocked']:
            break
    report = {**copy.deepcopy(state), 'actual_http_attempts': len(state['attempts']),
        'logical_decisions': len(state['calls']), 'search_calls': 0, 'fetch_calls': 0,
        'forecast_submissions': 0, 'semantic_quality_requires_manual_audit': True}
    report['business_gate_passed'] = len(state['cases']) == len(data['cases']) and all(c['pair_complete'] for c in state['cases'])
    report['frozen_arms'] = arms
    report['pending_case_ids'] = [c['id'] for c in data['cases'] if not any(r['case_id'] == c['id'] and r.get('pair_complete') for r in state['cases'])]
    report['batch_complete_with_preserved_queue'] = not state['blocked'] and all(r.get('pair_complete') for r in state['cases'])
    save(output / 'result.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--resume')
    parser.add_argument('--manifest', default=str(MANIFEST))
    parser.add_argument('--max-cases', type=int, default=5)
    args = parser.parse_args()
    result = run(args.output, os.environ['OPENROUTER_API_KEY'], args.manifest, resume=args.resume, max_cases=args.max_cases)
    print(json.dumps({k: result[k] for k in ('actual_http_attempts', 'logical_decisions', 'blocked', 'business_gate_passed')}))
    if not result['batch_complete_with_preserved_queue']:
        raise SystemExit(1)
