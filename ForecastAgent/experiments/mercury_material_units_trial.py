"""Frozen V2 versus sequential unit-aware V3 trial; no planning or acquisition."""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.providers import decisions
from ForecastAgent.supplement import acquisition_contract as base
from ForecastAgent.supplement import mercury_material_v2 as v2
from ForecastAgent.supplement import mercury_material_v3 as v3

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name('MERCURY_MATERIAL_UNITS8.json')


def run(output, key, manifest=MANIFEST):
    data = load(manifest)
    for filename, expected in data['frozen_code_sha256'].items():
        if hashlib.sha256((ROOT/filename).read_bytes().replace(b'\r\n',b'\n')).hexdigest() != expected:
            raise ValueError('frozen_code_changed')
    output = Path(output)
    if output.exists():raise ValueError('existing_trial_cannot_reset_budget')
    output.mkdir(parents=True)
    state = {'trial_id':data['trial_id'],'limits':data['limits'],'prior_experiments':data['prior_experiments'],
             'attempts':[],'calls':[],'cases':[],'blocked':False}
    save(output/'identity.json',{'manifest_sha256':hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        'frozen_code_sha256':data['frozen_code_sha256'],'scope':data['scope'],
        'independent_trial_no_old_quota_reset':True})
    active, starts = {}, {}
    deadline = time.monotonic()+data['limits']['seconds']

    def observer(event,record,token=None):
        if event == 'reserve':
            if len(state['attempts']) >= data['limits']['http']:raise RuntimeError('frozen_http_cap_exhausted')
            if record['request']['model'] != decisions.MODEL:raise RuntimeError('unexpected_model')
            token = len(state['attempts']); starts[token] = time.monotonic()
            state['attempts'].append({**active,'status':'reserved'})
            save(output/'state.json',state)
        filename = f'provider-{token+1:04d}.json'
        safe = {**record,'elapsed_seconds':time.monotonic()-starts[token]}
        save(output/filename,json.loads(json.dumps(safe).replace(key,'[REDACTED]')))
        state['attempts'][token] = {**active,'status':record['status'],'file':filename,
            'usage':record.get('response',{}).get('usage'),'http_status':record.get('http_status'),
            'elapsed_seconds':safe['elapsed_seconds']}
        save(output/'state.json',state)
        return token

    def execute(phase,prepared):
        if len(state['calls']) >= data['limits']['logical'] or time.monotonic() >= deadline:
            raise RuntimeError('frozen_trial_limit_exhausted')
        if len(json.dumps({'state':prepared['state'],'questions':prepared['questions']}).encode()) > data['limits']['request_bytes']:
            raise ValueError('request_byte_guard_exceeded')
        active['phase'] = phase
        call = {**active,'status':'reserved'}
        state['calls'].append(call); save(output/'state.json',state)
        try:
            response = decisions.decide(prepared['state'],prepared['questions'],key,observer)
            call['status'] = 'received'
            return response
        except Exception as exc:
            call.update(status='failed',error=str(exc)[:240])
            if not isinstance(exc,ValueError):state['blocked'] = True
            raise
        finally:save(output/'state.json',state)

    save(output/'state.json',state)
    for case in data['cases']:
        active.clear(); active['case_id'] = case['id']
        row = {'case_id':case['id'],'question_id':case['question_id'],'arms':{}}
        state['cases'].append(row)
        bundle,plan = case['bundle'],case['plan']
        baseline = v2.prepare(bundle,plan)
        row.update(need_count=len(plan['needs']),reading_sha256=base.sha(json.dumps(baseline['state']['reading'],sort_keys=True)))
        for arm in case['arm_order']:
            active['arm'] = arm
            try:
                if arm == 'v2':
                    prepared = baseline
                    save(output/(case['id']+'-v2-prepared.json'),prepared)
                    result = v2.bind(prepared,execute('evidence',prepared),bundle)
                else:
                    unit_request = v3.prepare_units(bundle,plan)
                    save(output/(case['id']+'-units-prepared.json'),unit_request)
                    units = execute('units',unit_request)
                    save(output/(case['id']+'-unit-response.json'),units)
                    prepared = v3.prepare(bundle,plan,units)
                    for field in ('question','needs','reading','rule_catalog'):
                        if prepared['state'][field] != baseline['state'][field]:
                            raise ValueError('paired_input_coverage_changed')
                    save(output/(case['id']+'-v3-prepared.json'),prepared)
                    result = v3.bind(prepared,execute('evidence',prepared),bundle)
                filename = case['id']+'-'+arm+'-result.json'
                save(output/filename,result)
                row['arms'][arm] = {'status':'received','file':filename,'application_status':result['application_status']}
            except Exception as exc:
                row['arms'][arm] = {'status':'failed','error':str(exc)[:240]}
                if not isinstance(exc,ValueError):state['blocked'] = True
            save(output/'state.json',state)
            if state['blocked']:break
        row['pair_complete'] = set(row['arms']) == {'v2','v3'} and all(a.get('application_status') == 'typed_review_complete' for a in row['arms'].values())
        save(output/'state.json',state)
        if state['blocked']:break
    report = {**state,'actual_http_attempts':len(state['attempts']),'logical_decisions':len(state['calls']),
              'search_calls':0,'fetch_calls':0,'forecast_submissions':0,
              'semantic_quality_requires_manual_audit':True}
    report['business_gate_passed'] = len(state['cases']) == len(data['cases']) and all(c['pair_complete'] for c in state['cases'])
    save(output/'result.json',report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    r = run(args.output,os.environ['OPENROUTER_API_KEY'])
    print(json.dumps({k:r[k] for k in ('actual_http_attempts','logical_decisions','blocked','business_gate_passed')}))
    if r['blocked'] or not r['business_gate_passed']:raise SystemExit(1)
