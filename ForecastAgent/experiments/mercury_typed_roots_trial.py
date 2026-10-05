"""Bounded V4/V5 identical-material regression; no acquisition or forecasts."""
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
from ForecastAgent.supplement import mercury_material_v4 as v4
from ForecastAgent.supplement import mercury_material_v5 as v5
from ForecastAgent.supplement import mercury_candidate_groups as groups
from ForecastAgent.supplement import mercury_wire as wire

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name('MERCURY_TYPED_ROOTS5.json')


def run(output, key, manifest=MANIFEST, *, resume=None):
    data = load(manifest)
    for filename, expected in data['frozen_code_sha256'].items():
        if hashlib.sha256((ROOT / filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise ValueError('frozen_code_changed')
    output = Path(output)
    if output.exists():
        raise ValueError('existing_trial_cannot_reset_budget')
    identity = hashlib.sha256(Path(manifest).read_bytes()).hexdigest()
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
    for case in data['cases']:
        active.clear()
        active['case_id'] = case['id']
        row = next((r for r in state['cases'] if r['case_id'] == case['id']), None)
        if row and row.get('pair_complete'):
            continue
        if row is None:
            row = {'case_id': case['id'], 'question_id': case['question_id'], 'arms': {}}
            state['cases'].append(row)
        bundle, plan = case['bundle'], case['plan']
        baseline = groups.prepare(v4.prepare(bundle, plan, case['contracts']))
        save(output / (case['id'] + '-frozen-input.json'), case)
        row['need_count'] = len(plan['needs'])
        for arm in case['arm_order']:
            if row['arms'].get(arm, {}).get('status') == 'received':
                continue
            active['arm'] = arm
            try:
                if arm == 'v4':
                    prepared = baseline
                    save(output / (case['id'] + '-v4-prepared.json'), prepared)
                    initial = output / (case['id'] + '-v4-initial-result.json')
                    if initial.exists():
                        result = load(initial)
                    else:
                        result = v4.bind(prepared, execute('proof', prepared), bundle)
                        save(initial, result)
                    coherence = v4.prepare_coherence(prepared, result)
                    if coherence['questions']:
                        save(output / (case['id'] + '-v4-coherence-prepared.json'), coherence)
                        result = v4.bind_coherence(result, coherence, execute('coherence', coherence))
                else:
                    contract_file = output / (case['id'] + '-typed-contract.json')
                    if contract_file.exists():
                        contract = load(contract_file)
                    else:
                        p = v5.prepare_contract(bundle, plan)
                        save(output / (case['id'] + '-contract-prepared.json'), p)
                        reply = execute('contract', p)
                        save(output / (case['id'] + '-contract-response.json'), reply)
                        contract = v5.bind_contract(p, reply)
                        save(contract_file, contract)
                    prepared = groups.prepare(v5.prepare(bundle, plan, contract))
                    for field in ('question', 'needs', 'reading', 'rule_catalog'):
                        if prepared['state'][field] != baseline['state'][field]:
                            raise ValueError('paired_input_coverage_changed')
                    save(output / (case['id'] + '-v5-prepared.json'), prepared)
                    result = v5.bind(prepared, execute('proof', prepared), bundle)
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
        row['pair_complete'] = set(row['arms']) == {'v4', 'v5'} and all(a.get('status') == 'received' for a in row['arms'].values())
        persist()
        if state['blocked']:
            break
    report = {**copy.deepcopy(state), 'actual_http_attempts': len(state['attempts']),
        'logical_decisions': len(state['calls']), 'search_calls': 0, 'fetch_calls': 0,
        'forecast_submissions': 0, 'semantic_quality_requires_manual_audit': True}
    report['business_gate_passed'] = len(state['cases']) == len(data['cases']) and all(c['pair_complete'] for c in state['cases'])
    save(output / 'result.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--resume')
    args = parser.parse_args()
    result = run(args.output, os.environ['OPENROUTER_API_KEY'], resume=args.resume)
    print(json.dumps({k: result[k] for k in ('actual_http_attempts', 'logical_decisions', 'blocked', 'business_gate_passed')}))
    if not result['business_gate_passed']:
        raise SystemExit(1)
