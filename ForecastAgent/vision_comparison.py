"""Isolated free vision comparison on immutable acquisition HTML snapshots."""
import argparse
import base64
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

MODEL = 'qwen/qwen3.8-27b:free'


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    from lxml import html
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    models = json.load(urlopen('https://openrouter.ai/api/v1/models', timeout=40))
    model = next((m for m in models['data'] if m['id'] == MODEL), None)
    save(args.output / 'catalog.json', model)
    if not model or 'image' not in model['architecture']['input_modalities']:
        raise RuntimeError('Requested free vision model is unavailable; no substitution')
    if any(float(model['pricing'].get(k, 0) or 0) for k in ('prompt', 'completion', 'image', 'request')):
        raise RuntimeError('Requested model is not free; refusing paid substitution')
    selected = {}
    for path in args.state.rglob('bundle.json'):
        bundle = json.loads(path.read_text(encoding='utf-8'))
        for page in bundle.get('pages', {}).values():
            url = page['url']
            if 'spce-20260630.htm' in url:
                selected['finance'] = page
            if 'hist.phtml' in url and 'month=8' in url and 'DEVC1' in url:
                selected['weather'] = page
    if set(selected) != {'finance', 'weather'}:
        raise RuntimeError('Frozen comparison sources are missing')
    cases = []
    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch()
        for key, source in selected.items():
            raw = base64.b64decode(source['raw_response_base64'])
            tree = html.fromstring(raw)
            tables = tree.xpath('//table')
            indices = [0] if key == 'weather' else [12, 13]
            images = []
            reference = []
            for index in indices:
                table = tables[index]
                label = ' '.join(table.text_content().split())
                if key == 'finance' and '2026' not in label:
                    raise RuntimeError('Frozen financial table layout changed')
                # Preserve original rows, spans and inline styles, without active resources.
                for node in table.xpath('.//script|.//style|.//img|.//iframe|.//link'):
                    node.getparent().remove(node)
                for node in table.iter():
                    for attribute in list(node.attrib):
                        if attribute.startswith('on') or attribute in {'src', 'href'}:
                            del node.attrib[attribute]
                markup = html.tostring(table, encoding='unicode')
                target = args.output / f'{key}-table-{index}.png'
                tab = browser.new_page(viewport={'width': 1800, 'height': 1200}, device_scale_factor=1)
                tab.route('**/*', lambda route: route.abort())
                tab.set_content('<html><head><meta charset="utf-8"><style>'
                    'body{margin:24px;font:18px Arial;background:white;color:black}'
                    'table{width:100%!important;border-collapse:collapse}'
                    'td,th{border:1px solid #aaa;padding:5px;font-size:16px!important}'
                    '</style></head><body><h2>' + key + ': ' + str(index) + '</h2>' + markup + '</body></html>')
                tab.screenshot(path=str(target), full_page=True)
                tab.close()
                images.append(target)
                reference.append({'table_index': index, 'rows': [
                    [' '.join(c.text_content().split()) for c in row.xpath('./th|./td')]
                    for row in table.xpath('.//tr')]})
            save(args.output / f'{key}-reference.json', {
                'source_url': source['url'], 'raw_sha256': hashlib.sha256(raw).hexdigest(),
                'rendering': 'Original frozen table DOM; neutral CSS, no external resources. Not a browser capture of the original page.',
                'tables': reference, 'original_documents': source.get('documents', []),
                'original_text': source.get('content', '')})
            prompt = (
                'Extract only visible data. Return one JSON object. Do not infer missing cells. '
                'Include uncertainty, missing fields and source image/table identifiers. '
                + ('For DEVC1 August 2026, return days:[{day,high,low}], all 31 August days. '
                   'Exclude preceding July placeholders. Also return units if explicitly visible; otherwise null.'
                   if key == 'weather' else
                   'For the balance sheet return Cash and cash equivalents, Total current assets, Total assets '
                   'with June 30 2026 and December 31 2025 columns. For the income statement return Revenue, '
                   'Total operating expenses, Operating loss, with all four three-month and six-month 2026/2025 columns. '
                   'Use rows:[{label,columns:[{period,value}]}]. Preserve negatives, units and missing units.'))
            cases.append((key, prompt, images, source.get('content', '')))
        browser.close()
    results = []
    for key, prompt, images, text in cases:
        for mode in ('text', 'vision'):
            content = [{'type': 'text', 'text': prompt}]
            if mode == 'text':
                content.append({'type': 'text', 'text': 'Saved original parser output:\n' + text})
            else:
                for image in images:
                    content.append({'type': 'text', 'text': 'Image ID: ' + image.name})
                    content.append({'type': 'image_url', 'image_url': {'url':
                        'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode()}})
            payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': content}],
                       'max_tokens': 6500, 'temperature': 0, 'provider': {'allow_fallbacks': False}}
            save(args.output / f'{key}-{mode}-request.json', payload)
            request = Request('https://openrouter.ai/api/v1/chat/completions',
                data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + os.environ['OPENROUTER_API_KEY']})
            start = time.monotonic()
            result = {'case': key, 'mode': mode, 'model': MODEL, 'http_attempts': 1,
                      'images': len(images) if mode == 'vision' else 0}
            try:
                with urlopen(request, timeout=150) as response:
                    result.update(http_status=response.status, response=json.load(response))
            except HTTPError as error:
                result.update(http_status=error.code, error_body=error.read().decode('utf-8', 'replace'))
            except Exception as error:
                result.update(error=type(error).__name__ + ': ' + str(error))
            result['seconds'] = round(time.monotonic() - start, 2)
            save(args.output / f'{key}-{mode}-response.json', result)
            results.append(result)
            save(args.output / 'results.json', results)
            print(json.dumps({k: result[k] for k in ('case', 'mode', 'http_attempts', 'seconds')}), flush=True)


if __name__ == '__main__':
    main()
