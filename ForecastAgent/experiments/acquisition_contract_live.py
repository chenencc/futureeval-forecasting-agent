"""Bounded live planning and annotation over frozen saved question materials."""
import argparse
import hashlib
import json
import os
import time
import shutil
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.supplement import acquisition_contract as ac
from ForecastAgent.readers.material_passages import spans
from ForecastAgent.supplement.research_loop import decode

MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'
MANIFEST = Path(__file__).with_name('ACQUISITION_CONTRACT_LIVE10.json')
ROOT = Path(__file__).resolve().parents[2]
LIMITS = {'logical': 20, 'http': 30, 'output_tokens': 4096, 'reasoning_tokens': 512,
          'seconds': 1200, 'reading_chars': 60000}


def planning_payload(payload):
    """Supply mechanical rule coordinates, never human semantic requirements."""
    result = dict(payload)
    result['rule_spans'] = [dict(field=field, **window) for field, text in payload['question'].items()
                            if text for window in spans(text)]
    return result


def run(output, key, manifest=MANIFEST, *, ids_pilot=False, parent=None):
    from ForecastAgent.providers.model import ask_model
    data = load(manifest)
    implementation = ac
    if ids_pilot:
        from ForecastAgent.supplement import acquisition_ids
        implementation = acquisition_ids
    if os.environ.get('FORECAST_MODEL') != MODEL or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '0':
        raise ValueError('fixed_super_without_fallback_required')
    for filename, expected in data['frozen_code_sha256'].items():
        raw = (ROOT / filename).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('frozen_contract_changed')
    output = Path(output)
    if output.exists():
        raise ValueError('existing_journal_no_implicit_reset')
    prior = None
    if ids_pilot:
        if parent is None:
            raise ValueError('exact_parent_required_no_budget_reset')
        parent = Path(parent)
        for filename, expected in data['parent_file_sha256'].items():
            if hashlib.sha256((parent / filename).read_bytes()).hexdigest() != expected:
                raise ValueError('parent_identity_or_state_changed')
        prior = load(parent / 'state.json')
        if (len(prior['attempts']) != data['parent_consumption']['http'] or
                len(prior['decisions']) != data['parent_consumption']['logical'] or
                any(a['status'] == 'reserved' for a in prior['attempts'])):
            raise ValueError('parent_usage_mismatch_or_unresolved_reservation')
    output.mkdir(parents=True)
    if prior is not None:
        for file in parent.iterdir():
            if file.is_file() and file.name not in ('state.json', 'identity.json', 'result.json'):
                shutil.copyfile(file, output / file.name)
    deadline = time.monotonic() + LIMITS['seconds']
    identity = {'schema': 'acquisition-contract-live-v1', 'model': MODEL, 'limits': LIMITS,
        'manifest_sha256': hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        'frozen_contract': data['frozen_code_sha256'], 'scope': data['scope']}
    identity['profile'] = 'ids_v2' if ids_pilot else 'offsets_v1'
    save(output / 'identity.json', identity)
    state = prior if prior is not None else {'decisions': [], 'attempts': [], 'cases': [], 'blocked': False}
    state['blocked'] = False
    before = {'logical': len(state['decisions']), 'http': len(state['attempts']), 'cases': len(state['cases'])}
    if ids_pilot:
        identity['parent_run_id'] = data['parent_run_id']
        identity['preserved_consumption'] = before
        identity['new_limits'] = data['new_limits']
        save(output / 'identity.json', identity)
    active = {}
    save(output / 'state.json', state)

    def observer(event, record, token=None):
        if event == 'reserve':
            if len(state['attempts']) >= LIMITS['http']:
                raise RuntimeError('shared_http_limit_reached')
            if ids_pilot and len(state['attempts']) - before['http'] >= data['new_limits']['http']:
                raise RuntimeError('new_http_limit_reached')
            if record['request']['model'] != MODEL:
                raise RuntimeError('model_changed')
            token = len(state['attempts'])
            state['attempts'].append({**active, 'status': 'reserved'})
            save(output / 'state.json', state)
        filename = f'provider-{token + 1:04d}.json'
        save(output / filename, json.loads(json.dumps(record).replace(key, '[REDACTED]')))
        state['attempts'][token] = {**active, 'status': record['status'], 'file': filename,
            'http_status': record.get('http_status'), 'usage': record.get('response', {}).get('usage')}
        save(output / 'state.json', state)
        return token

    def execute(phase, prompt, payload, tool):
        if len(state['decisions']) >= LIMITS['logical']:
            raise RuntimeError('shared_logical_limit_reached')
        if ids_pilot and len(state['decisions']) - before['logical'] >= data['new_limits']['logical']:
            raise RuntimeError('new_logical_limit_reached')
        active['phase'] = phase
        payload = planning_payload(payload) if phase == 'plan_needs' and not ids_pilot else payload
        decision = {**active, 'status': 'reserved', 'attempts_before': len(state['attempts'])}
        state['decisions'].append(decision)
        save(output / 'state.json', state)
        try:
            message = ask_model([{'role': 'system', 'content': prompt},
                {'role': 'user', 'content': json.dumps(payload)}], key, tools=[tool],
                forced_tool=tool['function']['name'], observer=observer, deadline=deadline,
                max_output_tokens=LIMITS['output_tokens'], reasoning={'max_tokens': LIMITS['reasoning_tokens']})
            raw = load(output / state['attempts'][-1]['file'])
            if (raw.get('response', {}).get('choices') or [{}])[0].get('finish_reason') == 'length':
                raise ValueError('output_truncated')
            result = decode(message, tool['function']['name'])
            decision.update(status='received', reply=result)
            return result
        except Exception as exc:
            decision.update(status='failed', error=str(exc)[:240])
            if not isinstance(exc, ValueError):
                state['blocked'] = True
            raise
        finally:
            decision['attempts_after'] = len(state['attempts'])
            save(output / 'state.json', state)

    for case in data['cases']:
        active.clear(); active['case_id'] = case['id']
        row = {'case_id': case['id'], 'question_id': case['question_id'], 'profile': identity['profile'],
               'status': 'started', 'checkpoints': []}
        state['cases'].append(row)
        save(output / 'state.json', state)

        def checkpoint(value):
            filename = f"{'ids-' if ids_pilot else ''}{case['id']}-{value['phase']}.json"
            save(output / filename, value)
            row['checkpoints'].append(filename)
            save(output / 'state.json', state)

        try:
            kwargs = {'max_chars': LIMITS['reading_chars']}
            if not ids_pilot:
                kwargs['remaining'] = {}
            result = implementation.agent_review(case['bundle'], execute, checkpoint, **kwargs)
            filename = f"{'ids-' if ids_pilot else ''}{case['id']}-ledger.json"
            save(output / filename, result)
            status = result.get('application_status', 'completed' if result['requirements']['needs'] else 'failed_requirements')
            row.update(status=status, ledger_file=filename,
                accepted_needs=len(result['requirements']['needs']),
                rejected_needs=len(result['requirements']['rejected_needs']),
                accepted_annotations=len(result['review']['annotations']),
                rejected_annotations=len(result['review']['rejected_annotations']),
                coverage=result['coverage'])
        except Exception as exc:
            row.update(status='failed', error=str(exc)[:240])
            if not isinstance(exc, ValueError):
                state['blocked'] = True
        finally:
            save(output / 'state.json', state)
        if state['blocked']:
            break
    report = {'schema': 'acquisition-contract-live-result-v1', 'identity': identity,
        'cases': state['cases'][before['cases']:], 'blocked': state['blocked'], 'logical_decisions': len(state['decisions']),
        'actual_http_attempts': len(state['attempts']),
        'new_logical_decisions': len(state['decisions']) - before['logical'],
        'new_http_attempts': len(state['attempts']) - before['http'],
        'known_tokens': sum((a.get('usage') or {}).get('total_tokens', 0) for a in state['attempts']),
        'unknown_usage_attempts': sum(not a.get('usage') for a in state['attempts']),
        'search_calls': 0, 'fetch_calls': 0, 'forecast_submissions': 0,
        'semantic_quality_requires_manual_audit': True}
    report['business_gate_passed'] = (len(report['cases']) == len(data['cases']) and
        all(c['status'] in ('reviewed_with_gaps', 'completed') for c in report['cases']))
    save(output / 'result.json', report)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    p.add_argument('--ids-pilot', action='store_true')
    p.add_argument('--parent')
    args = p.parse_args()
    manifest = MANIFEST.with_name('ACQUISITION_IDS_PILOT3.json') if args.ids_pilot else MANIFEST
    report = run(args.output, os.environ['OPENROUTER_API_KEY'], manifest, ids_pilot=args.ids_pilot, parent=args.parent)
    print(json.dumps({k: report[k] for k in ('blocked', 'logical_decisions', 'actual_http_attempts', 'known_tokens')}))
    if report['blocked'] or not report['business_gate_passed']:
        raise SystemExit(1)
