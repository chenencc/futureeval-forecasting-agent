"""Two frozen sequential Mercury protocol trials over identical saved materials."""
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
from ForecastAgent.supplement import mercury_material as v1
from ForecastAgent.supplement import mercury_material_v2 as v2
from ForecastAgent.supplement.research_loop import decode

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name('MERCURY_MATERIAL_TWO_ROUNDS.json')
SUPER = 'nvidia/nemotron-3-super-120b-a12b:free'


def run(output, key, manifest=MANIFEST):
    data = load(manifest)
    if os.environ.get('FORECAST_MODEL') != SUPER or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '0':
        raise ValueError('fixed_super_without_fallback_required')
    for filename, expected in data['frozen_code_sha256'].items():
        if hashlib.sha256((ROOT/filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise ValueError('frozen_code_changed')
    output = Path(output)
    if output.exists():
        raise ValueError('existing_trial_cannot_reset_budget')
    output.mkdir(parents=True)
    state = {'schema': 'mercury-material-two-rounds-v1', 'trial_id': data['trial_id'],
             'prior_experiments': data['prior_experiments'], 'limits': data['limits'],
             'attempts': [], 'logical_calls': [], 'rounds': [], 'blocked': False}
    save(output/'identity.json', {'manifest_sha256': hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
         'frozen_code_sha256': data['frozen_code_sha256'], 'scope': data['scope'],
         'independent_trial_no_old_quota_reset': True, 'trial_id': data['trial_id']})
    save(output/'state.json', state)
    active, reserved_at = {}, {}
    deadline = time.monotonic() + data['limits']['seconds']

    def observer(event, record, token=None):
        if event == 'reserve':
            if len(state['attempts']) >= data['limits']['http']:
                raise RuntimeError('trial_http_cap_exhausted')
            if sum(a['round'] == active['round'] for a in state['attempts']) >= data['limits']['http_per_round'][active['round']]:
                raise RuntimeError('round_http_cap_exhausted')
            model = SUPER if active['arm'] == 'planning' else decisions.MODEL
            if record['request']['model'] != model:
                raise RuntimeError('unexpected_model')
            token = len(state['attempts'])
            reserved_at[token] = time.monotonic()
            state['attempts'].append({**active, 'status': 'reserved'})
            save(output/'state.json', state)
        filename = f'provider-{token+1:04d}.json'
        record = {**record, 'elapsed_seconds': time.monotonic()-reserved_at[token]}
        save(output/filename, json.loads(json.dumps(record).replace(key, '[REDACTED]')))
        state['attempts'][token] = {**active, 'status': record['status'], 'file': filename,
             'http_status': record.get('http_status'), 'usage': record.get('response', {}).get('usage'),
             'elapsed_seconds': record['elapsed_seconds']}
        save(output/'state.json', state)
        return token

    def execute(arm, function):
        if time.monotonic() >= deadline or len(state['logical_calls']) >= data['limits']['logical']:
            raise RuntimeError('frozen_trial_limit_exhausted')
        active['arm'] = arm
        call = {**active, 'status': 'reserved', 'http_before': len(state['attempts'])}
        state['logical_calls'].append(call)
        save(output/'state.json', state)
        try:
            result = function()
            call['status'] = 'received'
            return result
        except Exception as exc:
            call.update(status='failed', error=str(exc)[:240])
            if not isinstance(exc, ValueError):
                state['blocked'] = True
            raise
        finally:
            call['http_after'] = len(state['attempts'])
            save(output/'state.json', state)

    for cohort in data['rounds']:
        round_row = {'id': cohort['id'], 'cases': [], 'scope': cohort['scope']}
        state['rounds'].append(round_row)
        for case in cohort['cases']:
            active.clear()
            active.update(round=cohort['id'], case_id=case['id'])
            row = {'case_id': case['id'], 'question_id': case['question_id'], 'arms': {}}
            round_row['cases'].append(row)
            bundle = case['bundle']
            prefix = cohort['id']+'-'+case['id']
            try:
                plan = case.get('plan')
                if plan is None:
                    definition = ids.tools()[0]
                    def planning():
                        message = ask_model([
                            {'role':'system', 'content':ids.PLAN_PROMPT},
                            {'role':'user', 'content':json.dumps({'question':bundle['request'],
                                                               'rule_catalog':ids.rule_catalog(bundle['request'])})}],
                            key, tools=[definition], forced_tool=definition['function']['name'],
                            observer=observer, max_output_tokens=4096, reasoning={'max_tokens':512})
                        record = load(output/state['attempts'][-1]['file'])
                        if (record.get('response', {}).get('choices') or [{}])[0].get('finish_reason') == 'length':
                            raise ValueError('planning_output_truncated')
                        reply = decode(message, definition['function']['name'])
                        save(output/(prefix+'-planning-reply.json'), reply)
                        needs, repairs = ids.envelope(reply, 'needs')
                        bound = ids.bind_needs(bundle['request'], needs)
                        bound['compatibility_repairs'] = repairs
                        if not bound['needs'] or bound['rejected_needs']:
                            raise ValueError('empty_or_partially_rejected_plan')
                        return bound
                    plan = execute('planning', planning)
                if len(plan['needs']) > data['limits']['needs_per_case']:
                    raise ValueError('need_count_guard_exceeded_no_silent_truncation')
                save(output/(prefix+'-frozen-input.json'), {'bundle':bundle, 'plan':plan})
                prepared = {'v1':v1.prepare(bundle, plan), 'v2':v2.prepare(bundle, plan)}
                for field in ('question', 'needs', 'reading', 'rule_catalog'):
                    if prepared['v1']['state'][field] != prepared['v2']['state'][field]:
                        raise ValueError('paired_input_coverage_changed')
                row.update(need_count=len(plan['needs']), reading_sha256=base.sha(json.dumps(prepared['v1']['state']['reading'], sort_keys=True)),
                           question_counts={arm:len(p['questions']) for arm,p in prepared.items()})
                for arm in case['arm_order']:
                    p = prepared[arm]
                    if len(json.dumps({'state':p['state'], 'questions':p['questions']}).encode()) > data['limits']['request_bytes']:
                        raise ValueError('request_byte_guard_exceeded')
                    save(output/(prefix+'-'+arm+'-prepared.json'), p)
                    implementation = v1 if arm == 'v1' else v2
                    try:
                        result = execute(arm, lambda: implementation.bind(p,
                            decisions.decide(p['state'], p['questions'], key, observer), bundle))
                        filename = prefix+'-'+arm+'-result.json'
                        save(output/filename, result)
                        row['arms'][arm] = {'status':'received', 'file':filename,
                                            'application_status':result['application_status']}
                    except Exception as exc:
                        row['arms'][arm] = {'status':'failed', 'error':str(exc)[:240]}
                    if state['blocked']:
                        break
            except Exception as exc:
                row['error'] = str(exc)[:240]
                if not isinstance(exc, ValueError):
                    state['blocked'] = True
            row['pair_complete'] = set(row['arms']) == {'v1','v2'} and all(
                a.get('application_status') == 'typed_review_complete' for a in row['arms'].values())
            save(output/'state.json', state)
            if state['blocked']:
                break
        round_row['format_gate_passed'] = len(round_row['cases']) == len(cohort['cases']) and all(c['pair_complete'] for c in round_row['cases'])
        save(output/(cohort['id']+'-result.json'), round_row)
        if state['blocked']:
            break
    report = {**state, 'actual_http_attempts':len(state['attempts']),
              'logical_decisions':len(state['logical_calls']), 'search_calls':0, 'fetch_calls':0,
              'forecast_submissions':0, 'semantic_quality_requires_manual_audit':True}
    report['business_gate_passed'] = len(state['rounds']) == len(data['rounds']) and all(
        r.get('format_gate_passed') for r in state['rounds'])
    save(output/'result.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run(args.output, os.environ['OPENROUTER_API_KEY'])
    print(json.dumps({k:result[k] for k in ('actual_http_attempts','logical_decisions','blocked','business_gate_passed')}))
    if result['blocked'] or not result['business_gate_passed']:
        raise SystemExit(1)
