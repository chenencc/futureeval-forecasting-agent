"""Independent bounded Mercury/Super material review trial; no collection or forecasts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.providers import decisions
from ForecastAgent.providers.model import ask_model
from ForecastAgent.supplement import acquisition_contract as base
from ForecastAgent.supplement import acquisition_ids as ids
from ForecastAgent.supplement import mercury_material as material
from ForecastAgent.supplement.research_loop import decode

MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'
MANIFEST = Path(__file__).with_name('MERCURY_MATERIAL_PILOT3.json')
ROOT = Path(__file__).resolve().parents[2]


def run(output, key, manifest=MANIFEST):
    data = load(manifest)
    if os.environ.get('FORECAST_MODEL') != MODEL or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '0':
        raise ValueError('fixed_super_required')
    for filename, expected in data['frozen_code_sha256'].items():
        if hashlib.sha256((ROOT/filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise ValueError('frozen_code_changed')
    output = Path(output)
    if output.exists():
        raise ValueError('existing_trial_cannot_reset_budget')
    output.mkdir(parents=True)
    state = {'schema': 'mercury-material-pilot-v1', 'trial_id': data['trial_id'],
        'attempts': [], 'decisions': [], 'cases': [], 'blocked': False,
        'prior_experiments': data['prior_experiments'], 'limits': data['limits']}
    save(output/'identity.json', {'manifest_sha256': hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        'frozen_code': data['frozen_code_sha256'], 'trial_id': data['trial_id'],
        'scope': data['scope'], 'independent_trial_no_old_quota_reset': True})
    save(output/'state.json', state)
    active = {}

    def observer(event, record, token=None):
        if event == 'reserve':
            limits = data['limits']
            if len(state['attempts']) >= limits['http'] or sum(a['arm'] == active['arm'] for a in state['attempts']) >= limits['http_per_arm']:
                raise RuntimeError('frozen_http_cap_exhausted')
            expected = decisions.MODEL if active['arm'] == 'mercury' else MODEL
            if record['request']['model'] != expected:
                raise RuntimeError('model_changed')
            token = len(state['attempts'])
            state['attempts'].append({**active, 'status': 'reserved'})
            save(output/'state.json', state)
        filename = f'provider-{token+1:04d}.json'
        safe = json.loads(json.dumps(record).replace(key, '[REDACTED]'))
        save(output/filename, safe)
        state['attempts'][token] = {**active, 'status': record['status'], 'file': filename,
            'http_status': record.get('http_status'), 'usage': record.get('response', {}).get('usage')}
        save(output/'state.json', state)
        return token

    for case in data['cases']:
        bundle, plan = case['bundle'], case['plan']
        prepared = material.prepare(bundle, plan)
        prepared_bytes = json.dumps({'model': decisions.MODEL, 'state': prepared['state'],
                                    'questions': prepared['questions']}).encode()
        if len(prepared_bytes) > data['limits']['decision_request_bytes']:
            raise ValueError('decision_request_byte_guard_exceeded')
        row = {'case_id': case['id'], 'question_id': case['question_id'], 'arms': {},
               'delivered_chars': prepared['state']['reading']['delivered_chars'],
               'reading_sha256': base.sha(json.dumps(prepared['state']['reading'], sort_keys=True)),
               'need_count': len(plan['needs']), 'mercury_question_count': len(prepared['questions'])}
        state['cases'].append(row)
        save(output/(case['id']+'-prepared.json'), prepared)
        for arm in case['arm_order']:
            if len(state['decisions']) >= data['limits']['logical']:
                state['blocked'] = True
                break
            active.clear(); active.update(case_id=case['id'], arm=arm)
            decision = {**active, 'status': 'reserved', 'attempts_before': len(state['attempts'])}
            state['decisions'].append(decision); save(output/'state.json', state)
            try:
                if arm == 'mercury':
                    response = decisions.decide(prepared['state'], prepared['questions'], key, observer)
                    result = material.bind(prepared, response, bundle)
                else:
                    payload = {'question': bundle['request'], 'needs': plan['needs'],
                        'reading': prepared['state']['reading'], 'selected_character_budget': 60000}
                    definition = ids.tools()[1]
                    message = ask_model([{'role':'system','content':ids.REVIEW_PROMPT},
                        {'role':'user','content':json.dumps(payload)}], key, tools=[definition],
                        forced_tool=definition['function']['name'], observer=observer,
                        max_output_tokens=4096, reasoning={'max_tokens':512})
                    record = load(output/state['attempts'][-1]['file'])
                    if (record.get('response', {}).get('choices') or [{}])[0].get('finish_reason') == 'length':
                        raise ValueError('output_truncated')
                    reply = decode(message, definition['function']['name'])
                    save(output/(case['id']+'-super-reply.json'), reply)
                    annotations, repairs = ids.envelope(reply, 'annotations')
                    review = ids.bind_review(plan, prepared['state']['reading'], annotations, bundle['pages'])
                    result = {'requirements': plan, 'reading': prepared['state']['reading'], 'review': review,
                              'coverage': ids.coverage(plan, review), 'compatibility_repairs': repairs}
                    result['application_status'] = ids.application_status(result)
                filename = case['id']+'-'+arm+'-result.json'
                save(output/filename, result)
                row['arms'][arm] = {'status': 'received', 'application_status': result['application_status'], 'file': filename}
                decision['status'] = 'received'
            except Exception as exc:
                row['arms'][arm] = {'status': 'failed', 'error': str(exc)[:240]}
                decision.update(status='failed', error=str(exc)[:240])
                # Invalid reply can be isolated; transport/account failures stop
                # remaining work rather than burning another model's allowance.
                if not isinstance(exc, ValueError):
                    state['blocked'] = True
            finally:
                decision['attempts_after'] = len(state['attempts']); save(output/'state.json', state)
            if state['blocked']:
                break
        if state['blocked']:
            break
    report = {**state, 'scope': data['scope'], 'actual_http_attempts': len(state['attempts']),
        'logical_decisions': len(state['decisions']), 'search_calls':0, 'fetch_calls':0,
        'forecast_submissions':0, 'semantic_quality_requires_manual_audit':True}
    report['business_gate_passed'] = len(state['cases']) == len(data['cases']) and all(
        set(c['arms']) == {'super', 'mercury'} and all(a.get('application_status') in
            ('typed_review_complete','reviewed_with_gaps') for a in c['arms'].values()) for c in state['cases'])
    save(output/'result.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = run(args.output, os.environ['OPENROUTER_API_KEY'])
    print(json.dumps({k: report[k] for k in ('actual_http_attempts','logical_decisions','blocked','business_gate_passed')}))
    if report['blocked'] or not report['business_gate_passed']:
        raise SystemExit(1)
