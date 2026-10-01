"""One physical, bounded tool-call health probe, independent of task budgets."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'


def probe(api_key, *, opener=urlopen):
    payload = {'model': MODEL, 'max_tokens': 256, 'temperature': 0,
        'messages': [{'role': 'user', 'content': 'Report service health using report_health with ready=true. No research.'}],
        'tools': [{'type': 'function', 'function': {'name': 'report_health',
            'description': 'Acknowledge this minimal tool-call health probe.',
            'parameters': {'type': 'object', 'properties': {'ready': {'type': 'boolean'}},
                           'required': ['ready'], 'additionalProperties': False}}}],
        'tool_choice': {'type': 'function', 'function': {'name': 'report_health'}}}
    report = {'schema': 'provider_health_v1', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'model': MODEL, 'physical_attempts': 0, 'healthy': False, 'request': payload,
        'search_calls': 0, 'task_budgets_changed': False, 'http_status': None,
        'limit': 'One successful probe does not establish sustained provider availability.'}
    if not api_key:
        report['state'] = 'missing_credentials'
        return report
    request = Request('https://openrouter.ai/api/v1/chat/completions',
        data=json.dumps(payload).encode(), method='POST',
        headers={'Authorization': 'Bearer '+api_key, 'Content-Type': 'application/json'})
    report['physical_attempts'] = 1
    try:
        try:
            with opener(request, timeout=120) as response:
                report['http_status'] = response.status
                raw = response.read(2_000_001)
        except HTTPError as exc:
            report['http_status'] = exc.code
            raw = exc.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Health response exceeded bounded size')
        report['response_sha256'] = hashlib.sha256(raw).hexdigest()
        report['response_text'] = raw.decode('utf-8', errors='replace').replace(api_key, '[REDACTED]')
        decoded = json.loads(report['response_text'])
        report['usage'] = decoded.get('usage')
        report['provider_error'] = decoded.get('error')
        calls = decoded.get('choices', [{}])[0].get('message', {}).get('tool_calls', [])
        valid = any(call.get('function', {}).get('name') == 'report_health' and
                    json.loads(call['function'].get('arguments', '{}')) == {'ready': True} for call in calls)
        report['healthy'] = valid and report['http_status'] == 200 and not report['provider_error']
        report['state'] = 'tool_call_acknowledged' if report['healthy'] else 'provider_error' if report['provider_error'] else 'tool_call_unavailable'
    except Exception as exc:
        report.update(state='transport_or_response_failure', error_type=type(exc).__name__)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = probe(os.getenv('OPENROUTER_API_KEY', ''))
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('state', 'healthy', 'physical_attempts', 'http_status')}))


if __name__ == '__main__':
    main()
