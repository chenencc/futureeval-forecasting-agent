"""Shared durable execution for typed, evidence-grounded forecast diagnostics."""
import json
import os
import time
from pathlib import Path

from ForecastAgent.analysis.pilot import Journal
from ForecastAgent.analysis.referenced import ModelJournal
from ForecastAgent.competition.queue import digest, load, save
from ForecastAgent.providers.decisions import decide
from ForecastAgent.providers.model import ModelRoute, ULTRA_MODEL, SUPER_MODEL, configured_model
from ForecastAgent.runtime.task_lock import task_lock


def run(bundle_path, output, *, protocol, prompt, packet_builder, record_tool, parse_report,
        decision_state, decision_questions, parse_decision, fuse_reports, ask=None, decision=None):
    from ForecastAgent.providers.ultra import ask_ultra
    ask = ask or ask_ultra
    decision = decision or decide
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    bundle = load(bundle_path)
    packet = packet_builder(bundle)
    packet['protocol'] = protocol
    identity = {'protocol': protocol, 'bundle_sha256': digest(bundle), 'packet_sha256': digest(packet),
                'prompt_sha256': digest(prompt), 'tool_sha256': digest(record_tool),
                'decision_questions_sha256': digest(decision_questions),
                'payload_policy': 'probability-clip-0.02-0.98-v1', 'primary_model': configured_model()}
    if configured_model() != ULTRA_MODEL:
        raise ValueError('This diagnostic requires the authorized Ultra-to-Super routing policy')
    with task_lock(output):
        if (output / 'manifest.json').exists() and load(output / 'manifest.json') != identity:
            raise ValueError('Frozen typed input identity changed')
        save(output / 'manifest.json', identity)
        save(output / 'evidence-packet.json', packet)
        route = ModelRoute()
        for path in sorted((output / 'reasoning-http').glob('*.json')):
            route.observe(load(path))
        journal = ModelJournal(output / 'reasoning-http', {ULTRA_MODEL, SUPER_MODEL})
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(packet)}]
        report = None
        # Received transport replay protects the reservation/parse crash window.
        if (output / 'analysis.json').exists():
            saved = load(output / 'analysis.json')
            report = parse_report({'tool_calls': [{'function': {'name': record_tool['function']['name'], 'arguments': json.dumps(saved)}}]}, packet)
        else:
            for path in sorted((output / 'reasoning-http').glob('*.json')):
                record = load(path)
                if record.get('status') == 'received':
                    try:
                        report = parse_report(record['response']['choices'][0]['message'], packet)
                    except (ValueError, KeyError, TypeError):
                        continue
                    break
            if report is None:
                for _ in range(3):
                    if not journal.remaining(route.model()):
                        break
                    message = ask(messages, os.environ['OPENROUTER_API_KEY'], tools=[record_tool],
                        forced_tool=record_tool['function']['name'], observer=journal, model_route=route,
                        max_output_tokens=6000, reasoning={'max_tokens': 1500}, deadline=time.monotonic() + 600)
                    try:
                        report = parse_report(message, packet)
                        break
                    except (ValueError, KeyError, TypeError) as exc:
                        messages.append({'role': 'user', 'content': 'Previous structured response was invalid: ' + str(exc) + '. Return a complete corrected result.'})
            if report is None:
                save(output / 'result.json', {'status': 'unavailable', 'no_forecasts_submitted': True})
                raise RuntimeError('No valid typed analysis within preserved lifetime caps')
            save(output / 'analysis.json', report)
        state = decision_state(report, packet)
        save(output / 'decision-state.json', state)
        mercury = None
        error = None
        try:
            if (output / 'decision-response.json').exists():
                response = load(output / 'decision-response.json')
            else:
                received = [load(p) for p in (output / 'mercury-http').glob('*.json') if load(p).get('status') == 'received']
                response = received[0]['response'] if received else decision(state, decision_questions,
                    os.environ['OPENROUTER_API_KEY'], Journal(output / 'mercury-http', 1))
                save(output / 'decision-response.json', response)
            from ForecastAgent.providers.decisions import validate
            validate(response, decision_questions)
            mercury = parse_decision(response)
        except Exception as exc:
            error = str(exc)
        result = {'schema': protocol, 'status': 'completed' if mercury is not None else 'partial',
                  'question_id': bundle['request']['id'], 'question_type': packet['question']['type'],
                  **fuse_reports(report, mercury), 'mercury_error': error,
                  'evaluation_warning': packet.get('evaluation_warning'), 'quality': 'diagnostic; quote presence checked, entailment and calibration unverified'}
        save(output / 'result.json', result)
        return result
