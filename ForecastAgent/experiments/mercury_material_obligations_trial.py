"""Frozen V3/V4 regression and repair-heldout material-obligation comparison."""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.providers import decisions
from ForecastAgent.providers.model import ask_model
from ForecastAgent.supplement import acquisition_contract as base
from ForecastAgent.supplement import acquisition_ids as ids
from ForecastAgent.supplement import mercury_material_v3 as v3
from ForecastAgent.supplement import mercury_material_v4 as v4
from ForecastAgent.supplement.research_loop import decode

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name('MERCURY_MATERIAL_OBLIGATIONS13.json')
SUPER = 'nvidia/nemotron-3-super-120b-a12b:free'


def run(output, key, manifest=MANIFEST):
    data = load(manifest)
    if os.environ.get('FORECAST_MODEL') != SUPER or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '0':
        raise ValueError('fixed_super_without_fallback_required')
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
            model = SUPER if active['phase'] in ('planning','compile') else decisions.MODEL
            if record['request']['model'] != model:raise RuntimeError('unexpected_model')
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

    def execute(phase,prepared,*,tool=None,prompt=None):
        if len(state['calls']) >= data['limits']['logical'] or time.monotonic() >= deadline:
            raise RuntimeError('frozen_trial_limit_exhausted')
        if len(json.dumps({'state':prepared['state'],'questions':prepared['questions']}).encode()) > data['limits']['request_bytes']:
            raise ValueError('request_byte_guard_exceeded')
        active['phase'] = phase
        call = {**active,'status':'reserved'}
        state['calls'].append(call); save(output/'state.json',state)
        try:
            if tool:
                message = ask_model([{'role':'system','content':prompt},
                    {'role':'user','content':json.dumps(prepared['state'])}],key,tools=[tool],
                    forced_tool=tool['function']['name'],observer=observer,
                    max_output_tokens=8192,reasoning={'max_tokens':512})
                record = load(output/state['attempts'][-1]['file'])
                if (record.get('response',{}).get('choices') or [{}])[0].get('finish_reason') == 'length':
                    raise ValueError('structured_output_truncated')
                response = decode(message,tool['function']['name'])
            else:
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
        row = {'case_id':case['id'],'question_id':case['question_id'],'cohort':case['cohort'],'arms':{}}
        state['cases'].append(row)
        bundle,plan = case['bundle'],case.get('plan')
        try:
            if plan is None:
                p = {'state':{'question':bundle['request'],'rule_catalog':ids.rule_catalog(bundle['request'])},'questions':{}}
                save(output/(case['id']+'-planning-prepared.json'),p)
                reply = execute('planning',p,tool=ids.tools()[0],prompt=ids.PLAN_PROMPT)
                save(output/(case['id']+'-planning-reply.json'),reply)
                proposals,repairs = ids.envelope(reply,'needs')
                plan = ids.bind_needs(bundle['request'],proposals)
                plan['compatibility_repairs'] = repairs
                if not plan['needs'] or plan['rejected_needs']:raise ValueError('empty_or_rejected_plan')
            if len(plan['needs']) > data['limits']['needs_per_case']:raise ValueError('need_limit_no_truncation')
            save(output/(case['id']+'-frozen-input.json'),{'bundle':bundle,'plan':plan})
            baseline = v3.v2.prepare(bundle,plan)
        except Exception as exc:
            row.update(pair_complete=False,error=str(exc)[:240])
            if not isinstance(exc,ValueError):state['blocked']=True
            save(output/'state.json',state)
            if state['blocked']:break
            continue
        row.update(need_count=len(plan['needs']),reading_sha256=base.sha(json.dumps(baseline['state']['reading'],sort_keys=True)))
        for arm in case['arm_order']:
            active['arm'] = arm
            try:
                if arm == 'v3':
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
                else:
                    compiler = {'state':v4.compile_payload(bundle,plan),'questions':{}}
                    save(output/(case['id']+'-compiler-prepared.json'),compiler)
                    reply = execute('compile',compiler,tool=v4.compile_tool(),prompt=v4.COMPILE_PROMPT)
                    save(output/(case['id']+'-compiler-reply.json'),reply)
                    contracts = v4.bind_contracts(bundle,plan,reply)
                    prepared = v4.prepare(bundle,plan,contracts)
                    if len(prepared['questions']) > data['limits']['proof_heads_per_case']:
                        raise ValueError('proof_head_limit_no_truncation')
                    for field in ('question','needs','reading','rule_catalog'):
                        if prepared['state'][field] != baseline['state'][field]:
                            raise ValueError('paired_input_coverage_changed')
                    save(output/(case['id']+'-v4-prepared.json'),prepared)
                    result = v4.bind(prepared,execute('proof',prepared),bundle)
                    save(output/(case['id']+'-v4-initial-result.json'),result)
                    coherence = v4.prepare_coherence(prepared,result)
                    if coherence['questions']:
                        save(output/(case['id']+'-coherence-prepared.json'),coherence)
                        result = v4.bind_coherence(result,coherence,execute('coherence',coherence))
                filename = case['id']+'-'+arm+'-result.json'
                save(output/filename,result)
                row['arms'][arm] = {'status':'received','file':filename,'application_status':result['application_status']}
            except Exception as exc:
                row['arms'][arm] = {'status':'failed','error':str(exc)[:240]}
                if not isinstance(exc,ValueError):state['blocked'] = True
            save(output/'state.json',state)
            if state['blocked']:break
        row['pair_complete'] = set(row['arms']) == {'v3','v4'} and all(a.get('application_status') == 'typed_review_complete' for a in row['arms'].values())
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
