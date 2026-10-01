"""Replay frozen text/image requests against a configurable free vision backend."""
import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = json.load(urlopen('https://openrouter.ai/api/v1/models', timeout=40))
    model = next((m for m in catalog['data'] if m['id'] == args.model), None)
    save(args.output / 'catalog.json', model)
    if not model or not args.model.endswith(':free') or 'image' not in model['architecture']['input_modalities']:
        raise RuntimeError('Requested free image-input model unavailable; refusing substitution')
    if any(float(model['pricing'].get(k, 0) or 0) for k in ('prompt', 'completion', 'image', 'request')):
        raise RuntimeError('Nonzero listed price; refusing paid trial')
    if 'reasoning' not in model.get('supported_parameters', []):
        raise RuntimeError('Backend cannot control reasoning through the advertised API')
    for path in args.source.iterdir():
        if path.suffix == '.png' or path.name.endswith('-reference.json'):
            shutil.copyfile(path, args.output / path.name)
    results = []
    for case in ('weather', 'finance'):
        for mode in ('text', 'vision'):
            original = json.loads((args.source / f'{case}-{mode}-request.json').read_text(encoding='utf-8'))
            payload = dict(original, model=args.model, reasoning={'enabled': False})
            save(args.output / f'{case}-{mode}-request.json', payload)
            record = {'case': case, 'mode': mode, 'model': args.model,
                'messages_sha256': hashlib.sha256(json.dumps(original['messages'], sort_keys=True).encode()).hexdigest(),
                'same_input_as_qwen': payload['messages'] == original['messages'],
                'reasoning_enabled': False, 'max_http_attempts': 2, 'attempts': []}
            for index in range(2):
                attempt = {'index': index + 1}
                record['attempts'].append(attempt)
                # Persist the reservation before dispatch; no hidden transport retries.
                save(args.output / f'{case}-{mode}-response.json', record)
                request = Request('https://openrouter.ai/api/v1/chat/completions',
                    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + os.environ['OPENROUTER_API_KEY']})
                started = time.monotonic()
                try:
                    with urlopen(request, timeout=150) as response:
                        attempt.update(http_status=response.status, response=json.load(response))
                    choices = attempt['response'].get('choices') or []
                    if choices:
                        choice = choices[0]
                        attempt['finish_reason'] = choice.get('finish_reason')
                        text = (choice.get('message') or {}).get('content') or ''
                        if text.strip().startswith('```'):
                            text = '\n'.join(text.strip().splitlines()[1:-1])
                        try:
                            attempt['parsed_output'] = json.loads(text)
                            attempt['complete_json'] = choice.get('finish_reason') == 'stop'
                        except ValueError:
                            attempt['complete_json'] = False
                    else:
                        attempt['complete_json'] = False
                except HTTPError as error:
                    attempt.update(http_status=error.code, error_body=error.read().decode('utf-8', 'replace'))
                except Exception as error:
                    attempt['error'] = type(error).__name__ + ': ' + str(error)
                attempt['seconds'] = round(time.monotonic() - started, 2)
                save(args.output / f'{case}-{mode}-response.json', record)
                if attempt.get('http_status') not in (429, 500, 502, 503, 504):
                    break
                if index == 0:
                    time.sleep(20)
            results.append(record)
            save(args.output / 'results.json', results)
            print(json.dumps({'case': case, 'mode': mode, 'attempts': len(record['attempts']),
                              'complete_json': record['attempts'][-1].get('complete_json', False)}), flush=True)


if __name__ == '__main__':
    main()
