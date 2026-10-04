"""Bounded live planning and annotation over frozen saved question materials."""
import argparse
import hashlib
import json
import os
import time
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


def run(output, key, manifest=MANIFEST):
    from ForecastAgent.providers.model import ask_model
    data = load(manifest)
    if os.environ.get('FORECAST_MODEL') != MODEL or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') != '0':
        raise ValueError('fixed_super_without_fallback_required')
    for filename, expected in data['frozen_code_sha256'].items():
        raw = (ROOT / filename).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('frozen_contract_changed')
    output = Path(output)
    if output.exists():
        raise ValueError('existing_journal_no_implicit_reset')
    output.mkdir(parents=True)
    deadline = time.monotonic() + LIMITS['seconds']
    identity = {'schema': 'acquisition-contract-live-v1', 'model': MODEL, 'limits': LIMITS,
        'manifest_sha256': hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        'frozen_contract': data['frozen_code_sha256'], 'scope': data['scope']}
    save(output / 'identity.json', identity)
    state = {'decisions': [], 'attempts': [], 'cases': [], 'blocked': False}
    active = {}
    save(output / 'state.json', state)

    def observer(event, record, token=None):
        if event == 'reserve':
            if len(state['attempts']) >= LIMITS['http']:
                raise RuntimeError('shared_http_limit_reached')
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
        active['phase'] = phase
        payload = planning_payload(payload) if phase == 'plan_needs' else payload
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
        row = {'case_id': case['id'], 'question_id': case['question_id'], 'status': 'started', 'checkpoints': []}
        state['cases'].append(row)
        save(output / 'state.json', state)

        def checkpoint(value):
            filename = f"{case['id']}-{value['phase']}.json"
            save(output / filename, value)
            row['checkpoints'].append(filename)
            save(output / 'state.json', state)

        try:
            result = ac.agent_review(case['bundle'], execute, checkpoint,
                max_chars=LIMITS['reading_chars'], remaining={})
            filename = f"{case['id']}-ledger.json"
            save(output / filename, result)
            row.update(status='completed', ledger_file=filename,
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
        'cases': state['cases'], 'blocked': state['blocked'], 'logical_decisions': len(state['decisions']),
        'actual_http_attempts': len(state['attempts']),
        'known_tokens': sum((a.get('usage') or {}).get('total_tokens', 0) for a in state['attempts']),
        'unknown_usage_attempts': sum(not a.get('usage') for a in state['attempts']),
        'search_calls': 0, 'fetch_calls': 0, 'forecast_submissions': 0,
        'semantic_quality_requires_manual_audit': True}
    save(output / 'result.json', report)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    report = run(args.output, os.environ['OPENROUTER_API_KEY'])
    print(json.dumps({k: report[k] for k in ('blocked', 'logical_decisions', 'actual_http_attempts', 'known_tokens')}))
    if report['blocked']:
        raise SystemExit(1)
