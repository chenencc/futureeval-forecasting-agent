"""Offline capture/consumption audit; lexical hints do not certify relevance."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from ForecastAgent.competition.queue import load, save


def issuer_domain(url, issuer):
    host = (urlsplit(url).hostname or '').lower()
    domain = 'tesla.com' if issuer == 'Tesla' else 'apple.com' if issuer == 'Apple' else None
    return bool(domain and (host == domain or host.endswith('.' + domain)))


def page_view(url, page, issuer):
    text = page.get('content', '')
    hits = []
    for pattern in (r'diluted.{0,90}', r'GAAP.{0,90}', r'total revenues?.{0,90}',
                    r'net sales.{0,90}', r'fiscal.{0,90}', r'guidance.{0,90}', r'2026.{0,90}'):
        hits.extend(m.group() for m in list(re.finditer(pattern, text, re.I))[:3])
    return {'url': url, 'final_url': page.get('final_url'),
            'issuer_domain': issuer_domain(page.get('final_url') or url, issuer),
            'sec_domain': (urlsplit(url).hostname or '') in ('www.sec.gov', 'sec.gov'),
            'capture_method': page.get('capture_method'), 'content_type': page.get('content_type'),
            'retrieved_at_utc': page.get('retrieved_at_utc'), 'source_sha256': page.get('sha256'),
            'body_chars': len(text), 'body_diagnostics': page.get('body_diagnostics'),
            'document_count': len(page.get('documents', [])),
            'financial_text_hints': hits[:15],
            'year_mentions': dict(Counter(re.findall(r'\b20[0-9]{2}\b', text))),
            'target_period_verified': False, 'preview': text[:900]}


def run(root):
    root = Path(root)
    manifest = load(root / 'manifest.json')
    reports = []
    for row in manifest['rows']:
        folder = root / 'tasks' / row['id']
        result = load(folder / 'collection-result.json')
        native = folder / 'retrieval/release-1.0.5'
        raw = load(native / 'collection/bundle.json')
        marker = load(folder / 'retrieval/bundle.json').get('release_acquisition') or {}
        package = load(native / marker['package_file']) if marker.get('package_file') else (
            load(native / 'package.json') if (native / 'package.json').exists() else raw)
        issuer = 'Tesla' if row['id'] in ('46190', '46197') else 'Apple'
        pages = [page_view(url, page, issuer) for url, page in package.get('pages', {}).items()]
        journals = []
        for attempt in raw.get('model_attempts', []):
            path = (native / 'collection' / attempt['path']).resolve()
            if not path.is_relative_to((native / 'collection').resolve()):
                raise ValueError('Journal escaped the task')
            if hashlib.sha256(path.read_bytes()).hexdigest() != attempt['sha256']:
                raise ValueError('Provider journal changed')
            record = load(path)
            request = record.get('request', {})
            journals.append({'model': request.get('model'), 'status': record.get('status'),
                'payload_sha256': hashlib.sha256(json.dumps(request).encode('utf-8')).hexdigest(),
                'usage': record.get('usage') or (record.get('response') or {}).get('usage'),
                'tool_calls': [t.get('function', {}).get('name') for choice in
                    (record.get('response') or {}).get('choices', []) for t in
                    (choice.get('message') or {}).get('tool_calls', [])]})
        captures = [p for p in pages if (p.get('body_diagnostics') or {}).get('usable_text')]
        searches = [{k: s.get(k) for k in ('query', 'topic', 'search_role', 'status', 'options', 'error')}
                    for s in raw.get('searches', [])]
        requests = load(root / row['input'])
        reports.append({'id': row['id'], 'issuer': issuer, 'title': row['title'],
                        'state': result['status'], 'collector_termination': (raw.get('result') or {}).get('termination_reason'),
                        'target_unit': requests.get('unit'), 'type': requests['question_type'],
                        'original_rules': requests['resolution_criteria'],
                        'input_sha256': row['input_sha256'],
                        'resources': result.get('resources'), 'provider_journals': journals,
                        'search_requests': searches, 'pages': pages,
                        'readable_pages': len(captures),
                        'readable_issuer_domain_pages': sum(p['issuer_domain'] for p in captures),
                        'readable_pdf_pages': sum(p['content_type'] == 'application/pdf' for p in captures),
                        'gaps': package.get('gaps', []),
                        'material_needs': raw.get('plan', []),
                        'material_assessments': raw.get('material_assessments', []),
                        'supplement_manifest': str(native / 'supplement'),
                        'analysis_run': False, 'submitted': False})
    receipts = [load(p) for p in (root / 'provider-transport').glob('*/attempts/*.json')]
    exa_receipts = [load(p) for p in (root / 'exa-transport').rglob('attempts/*.json')]
    shared = sorted(set(p['source_sha256'] for p in reports[0]['pages'] if p['source_sha256']) &
                    set(p['source_sha256'] for p in reports[1]['pages'] if p['source_sha256']))
    report = {'schema': 'market-pulse-pilot-capture-audit-v1',
              'reviewed_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Saved raw capture, provider usage and gaps; relevance requires manual reading.',
              'states': dict(Counter(r['state'] for r in reports)),
              'model_backend_counts': dict(Counter(a['model'] for r in reports for a in r['provider_journals'])),
              'known_model_tokens': sum((a.get('usage') or {}).get('total_tokens', 0) for r in reports for a in r['provider_journals']),
              'unknown_model_usage_attempts': sum(not isinstance((a.get('usage') or {}).get('total_tokens'), int) for r in reports for a in r['provider_journals']),
              'provider_http_receipts': dict(Counter(f"{r.get('provider')}:{r.get('endpoint')}:{r.get('http_status')}" for r in receipts)),
              'transport_receipt_coverage_note': 'Initial executed runner installed transport after some release imports. Receipts cover Extract only; successful model journals and basic Search records remain available. This is fixed before imports in the next runner revision, without rerunning this pilot.',
              'exa_transport_receipts': len(exa_receipts),
              'logical_tavily_basic': sum(len(load(root / 'tasks' / r['id'] / 'retrieval/release-1.0.5/collection/bundle.json').get('searches', [])) for r in reports),
              'logical_exa': sum(len(load(root / 'tasks' / r['id'] / 'retrieval/release-1.0.5/collection/bundle.json').get('exa_searches', [])) for r in reports),
              'tavily_limit_respected': all((r.get('resources') or {}).get('tavily_basic_attempts', 0) <= 3 for r in reports),
              'exa_limit_respected': all((r.get('resources') or {}).get('exa_attempts', 0) <= 1 for r in reports),
              'tesla_shared_capture_hashes': shared,
              'cross_task_cache_enabled': False, 'financial_customization': 'Unchanged release acquisition baseline',
              'analysis_run': False, 'submitted': False, 'budget_reset': False, 'questions': reports}
    save(root / 'audit.json', report)
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    report = run(args.root)
    print(json.dumps({k: v for k, v in report.items() if k != 'questions'}, indent=2))
