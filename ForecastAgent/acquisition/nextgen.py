"""Experimental acquisition tools and release-pipeline adapter; no forecasts."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.supplement.stage import digest, save
from ForecastAgent.readers.browser import BrowserCaptureError
from ForecastAgent.readers.crawl4ai import render_page, enrich_html, discover_links, BACKEND_VERSION, ADAPTER_VERSION


def now():
    return datetime.now(timezone.utc).isoformat()


def code_identity():
    root = Path(__file__).resolve().parents[2]
    names = ('ForecastAgent/readers/crawl4ai.py', 'ForecastAgent/acquisition/nextgen.py',
             'ForecastAgent/readers/material_structure.py', 'ForecastAgent/readers/network_data.py')
    return {name: hashlib.sha256((root/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for name in names}


def capability_catalog():
    """Program-owned bounds accompany each capability, independent of model choice."""
    return {'schema': 'nextgen_acquisition_tools_v1', 'backend': 'crawl4ai', 'backend_version': BACKEND_VERSION,
        'tools': [
            {'name': 'render_public_source', 'purpose': 'Render a known public HTML URL. Optional CSS wait and bounded scrolling.',
             'cost': {'browser_reservations': 1, 'max_dependency_requests': 25, 'models': 0, 'tavily': 0, 'exa': 0}},
            {'name': 'reparse_saved_html', 'purpose': 'Verify saved bytes, then add Markdown and structured tables without network.',
             'cost': {'network': 0, 'models': 0, 'searches': 0}},
            {'name': 'discover_saved_details', 'purpose': 'Rank observed same-host links with parent hashes; fetch selected details under the remaining page allowance.',
             'cost': {'network': 0, 'models': 0, 'searches': 0}},
            {'name': 'discover_embedded_resources', 'purpose': 'List observed iframe, alternate and browser data-request URLs with source provenance. Cross-origin resources require public destination checks.',
             'cost': {'network': 0, 'models': 0, 'searches': 0}},
            {'name': 'inspect_source_sections', 'purpose': 'Read source-bound article, table, navigation and footer roles; retain every original body.',
             'cost': {'network': 0, 'models': 0, 'searches': 0}},
            {'name': 'read_structured_response', 'purpose': 'Read saved JSON/CSV rows with exact requested parameters and separate reported metadata. Errors are gaps.',
             'cost': {'network': 0, 'models': 0, 'searches': 0}},
        ], 'limits': {'max_pages_per_experiment': 8, 'max_observed_link_depth': 1,
                      'max_scroll_steps': 4, 'automatic_retries': 0},
        'note': 'Search discovery and adequacy reasoning remain with the existing acquisition agent. Readability is not truth or relevance.'}


def metrics(snapshot):
    observed = snapshot.get('capture_status') or {}
    return {'readable': bool(observed.get('usable_text', snapshot.get('body_diagnostics', {}).get('usable_text'))),
            'body_state': observed.get('body_state', snapshot.get('body_diagnostics', {}).get('state')),
            'capture_category': observed.get('category'), 'render_state': snapshot.get('render_state'),
            'render_complete': observed.get('render_complete'),
            'body_chars': len(snapshot.get('content', '')),
            'markdown_chars': len(snapshot.get('crawl4ai', {}).get('raw_markdown', '')),
            'structured_tables': len(snapshot.get('crawl4ai', {}).get('structured_tables', [])),
            'structured_rows': sum(len(t['rows']) for t in snapshot.get('crawl4ai', {}).get('structured_tables', [])),
            'legacy_table_documents': sum(d.get('metadata', {}).get('format') == 'html_table' and
                d.get('metadata', {}).get('extraction_engine') != ADAPTER_VERSION for d in snapshot.get('documents', [])),
            'dependency_requests': snapshot.get('browser_audit', {}).get('allowed_requests',
                sum(bool(r.get('allowed')) for r in snapshot.get('browser_audit', {}).get('requests', []))),
            'capture_method': snapshot.get('capture_method'), 'dom_sha256': snapshot.get('sha256'),
            'tables_truncated': snapshot.get('crawl4ai', {}).get('tables_truncated')}


def validate_manifest(manifest):
    if set(manifest) - {'schema', 'sources', 'query', 'max_pages', 'follow_details', 'mode'}:
        raise ValueError('Unknown source-manifest fields')
    if manifest.get('mode', 'live') != 'live':
        raise ValueError('Use verified saved-byte reparse for historical material; current rendering is not historical proof')
    sources = manifest.get('sources')
    if not isinstance(sources, list) or not sources or len(sources) > 8:
        raise ValueError('Supply one to eight frozen source URLs')
    if type(manifest.get('max_pages', 4)) is not int or not 1 <= manifest.get('max_pages', 4) <= 8:
        raise ValueError('Page allowance must be from one to eight')
    if len(sources) > manifest.get('max_pages', 4):
        raise ValueError('Source list exceeds the frozen page allowance')
    if type(manifest.get('follow_details', False)) is not bool:
        raise ValueError('follow_details must be a boolean')
    query = manifest.get('query', '')
    if not isinstance(query, str) or len(query) > 3000:
        raise ValueError('Query must be bounded text')
    seen = set()
    for row in sources:
        if not isinstance(row, dict) or set(row) - {'url', 'wait_for_css', 'scroll', 'origin'}:
            raise ValueError('Unknown source options; executable scripts are not tool arguments')
        parts = urlsplit(row.get('url', ''))
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            raise ValueError('Expected an uncredentialed HTTP URL')
        if row['url'] in seen:
            raise ValueError('Duplicate source URL')
        seen.add(row['url'])
        if type(row.get('scroll', False)) is not bool:
            raise ValueError('scroll must be a boolean')
        selector = row.get('wait_for_css')
        if selector is not None and (not isinstance(selector, str) or not 1 <= len(selector) <= 200):
            raise ValueError('Invalid CSS wait selector')


def verify_capture(root, attempt):
    path = (root/attempt['capture_file']).resolve()
    if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != attempt['capture_json_sha256']:
        raise ValueError('Saved capture changed or escaped the output root')
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    raw = base64.b64decode(snapshot['raw_response_base64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != snapshot['sha256']:
        raise ValueError('Archived DOM hash mismatch')
    return snapshot


def collect_sources(manifest, root, *, backend='crawl4ai', browser_channel='chromium', _renderer=None):
    """Bounded experiment journal; resumed failures and reservations are not rerun."""
    validate_manifest(manifest)
    if backend not in {'crawl4ai', 'release_playwright'}:
        raise ValueError('Unsupported capture backend')
    if browser_channel not in {'chromium', 'chrome', 'msedge'} or (backend == 'release_playwright' and browser_channel != 'chromium'):
        raise ValueError('Unsupported browser channel for this backend')
    if backend == 'release_playwright':
        from ForecastAgent.readers.browser import render_page as renderer
    else:
        renderer = render_page
    renderer = _renderer or renderer
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    identity = {'schema': 'nextgen_source_experiment_v1', 'manifest': manifest,
                'backend': backend, 'backend_version': BACKEND_VERSION if backend == 'crawl4ai' else 'release_v1.0.5',
                'browser_channel': browser_channel,
                'code_sha256': code_identity(), 'provider_calls': {'models': 0, 'tavily': 0, 'exa': 0},
                'request_limit_per_page': 25, 'timeout_ms': 20000}
    with task_lock(root):
        identity_path = root/'identity.json'
        if identity_path.exists() and json.loads(identity_path.read_text(encoding='utf-8')) != identity:
            raise ValueError('Frozen input, code or policy changed; refuse a silent budget restart')
        save(identity_path, identity)
        state_path = root/'state.json'
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
            'schema': 'nextgen_source_ledger_v1', 'attempts': [], 'frontier': [dict(row, depth=0) for row in manifest['sources']]}
        if state.get('identity_sha256') not in {None, digest(identity)}:
            raise ValueError('Ledger identity mismatch')
        state['identity_sha256'] = digest(identity)
        for attempt in state['attempts']:
            if attempt.get('capture_file'):
                verify_capture(root, attempt)
        for row in state['frontier']:
            if any(a['url'] == row['url'] for a in state['attempts']):
                continue
            if len(state['attempts']) >= manifest.get('max_pages', 4):
                break
            attempt = {'url': row['url'], 'depth': row['depth'], 'origin': row.get('origin'),
                       'parent_dom_sha256': row.get('parent_dom_sha256'), 'status': 'reserved', 'started_at_utc': now()}
            state['attempts'].append(attempt)
            save(state_path, state)
            try:
                options = {'retrieved_at': now(), 'request_limit': 25, 'timeout_ms': 20000}
                if backend == 'crawl4ai':
                    options.update(query=manifest.get('query', ''), wait_for_css=row.get('wait_for_css'), scroll=row.get('scroll', False), browser_channel=browser_channel)
                snapshot = renderer(row['url'], **options)
                key = hashlib.sha256(row['url'].encode()).hexdigest()
                snapshot['nextgen_provenance'] = {'input_identity_sha256': digest(identity),
                    'origin': row.get('origin'), 'depth': row['depth'], 'parent_dom_sha256': row.get('parent_dom_sha256')}
                path = root/'captures'/f'{key}.json'
                save(path, snapshot)
                observed = metrics(snapshot)
                status = 'partial' if observed['readable'] and observed['render_state'] == 'deadline_partial_dom' else 'readable' if observed['readable'] else 'unreadable'
                attempt.update(status=status,
                               capture_file=f'captures/{key}.json', capture_json_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                               metrics=observed)
                if backend == 'crawl4ai' and manifest.get('follow_details') and row['depth'] == 0:
                    candidates = discover_links(snapshot, query=manifest.get('query', ''), limit=5)
                    for candidate in candidates:
                        if not any(r['url'] == candidate['url'] for r in state['frontier']):
                            state['frontier'].append(dict(candidate, depth=1, origin='observed_parent_link'))
            except Exception as exc:
                attempt.update(status='failed', error=type(exc).__name__, detail=str(exc)[:1000])
                if isinstance(exc, BrowserCaptureError):
                    attempt['browser_audit'] = exc.audit
            finally:
                attempt['finished_at_utc'] = now()
                save(state_path, state)
        counts = {name: sum(a['status'] == name for a in state['attempts']) for name in ('readable', 'partial', 'unreadable', 'failed', 'reserved')}
        report = {'schema': 'nextgen_source_report_v1', 'backend': backend, 'counts': counts,
            'attempts': state['attempts'], 'unused_page_allowance': manifest.get('max_pages', 4) - len(state['attempts']),
            'unvisited_frontier': [r for r in state['frontier'] if not any(a['url'] == r['url'] for a in state['attempts'])],
            'provider_calls': identity['provider_calls'], 'analysis_run': False, 'submitted': False,
            'scope': 'Technical source capture. No relevance, truth or prediction-quality claim.'}
        save(root/'report.json', report)
        return report


@contextmanager
def supplement_backend(query=''):
    """Process-local opt-in seam; callers run one pipeline per process."""
    from ForecastAgent.supplement import stage
    original = stage.render_page
    def adapter(url, **kwargs):
        snapshot = render_page(url, query=query, **kwargs)
        if not snapshot['capture_status']['usable_text']:
            # The legacy stage rechecks content independently. Retain the body
            # for inspection while preventing denied text from being admitted.
            snapshot = dict(snapshot, excluded_body={'content': snapshot.get('content', ''),
                'documents': snapshot.get('documents', []), 'reason': snapshot['capture_status']},
                content='', documents=[])
        return snapshot
    stage.render_page = adapter
    try:
        yield
    finally:
        stage.render_page = original


def pipeline_request(request):
    """Use identical policy identity for preflight and actual execution."""
    return dict(request, source_capture_policy={
        'browser_backend': 'crawl4ai', 'version': BACKEND_VERSION, 'code_sha256': code_identity(),
        'search_budget_changed': False, 'analysis_changed': False})


def run_pipeline(request, root, *, supplement_network=False):
    """Keep existing agent/search/HTTP allowances; replace only browser repair."""
    from ForecastAgent.acquisition.pipeline import run
    candidate = pipeline_request(request)
    with supplement_backend(request['question']):
        report = run(candidate, root, supplement_network=supplement_network)
    root = Path(root)
    if report.get('state') != 'complete':
        return report
    package_path = root/'package.json'
    original_bytes = package_path.read_bytes()
    original = json.loads(original_bytes)
    overlay = dict(original, pages=dict(original.get('pages', {})))
    rows = []
    for url, page in original.get('pages', {}).items():
        if page.get('content_type') not in {'text/html', 'application/xhtml+xml'}:
            continue
        try:
            enriched = enrich_html(page, query=request['question'])
            overlay['pages'][url] = enriched
            rows.append({'url': url, 'state': 'enriched', **metrics(enriched)})
        except Exception as exc:
            rows.append({'url': url, 'state': 'reparse_gap', 'error': type(exc).__name__, 'detail': str(exc)[:500]})
    overlay['nextgen_lineage'] = {'parent_package_sha256': hashlib.sha256(original_bytes).hexdigest(),
                                 'policy': candidate['source_capture_policy'], 'model_calls': 0, 'network_calls': 0}
    save(root/'nextgen-package.json', overlay)
    result = {'original_report': report, 'reparse_results': rows,
              'parent_package_sha256': overlay['nextgen_lineage']['parent_package_sha256'],
              'scope': 'Existing agent and allowances, optional browser repair, then local HTML enrichment. No analysis or submission.'}
    save(root/'nextgen-report.json', result)
    return result


def replay_saved_packages(paths, root, *, max_pages=100):
    """Pair derived readers on identical archived bytes; no collection or scoring."""
    if not 1 <= max_pages <= 100:
        raise ValueError('Offline replay supports one to one hundred pages')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    parents = [(Path(path), Path(path).read_bytes()) for path in paths]
    frozen = {'schema': 'crawl4ai_saved_replay_v1', 'code_sha256': code_identity(),
              'max_pages': max_pages, 'parents': [
                  {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}
                  for path, raw in parents]}
    with task_lock(root):
        ipath = root/'identity.json'
        if ipath.exists() and json.loads(ipath.read_text(encoding='utf-8')) != frozen:
            raise ValueError('Saved replay input or code changed')
        save(ipath, frozen)
        rows = []
        eligible = 0
        for parent_path, parent_raw in parents:
            package = json.loads(parent_raw)
            for url, page in package.get('pages', {}).items():
                if page.get('content_type') not in {'text/html', 'application/xhtml+xml'}:
                    continue
                eligible += 1
                if len(rows) >= max_pages:
                    continue
                row = {'url': url, 'parent_package': str(parent_path.resolve()),
                       'parent_package_sha256': hashlib.sha256(parent_raw).hexdigest(),
                       'original': metrics(page)}
                try:
                    child = enrich_html(page)
                    key = hashlib.sha256((row['parent_package_sha256'] + url).encode()).hexdigest()
                    output = root/'captures'/f'{key}.json'
                    save(output, child)
                    row.update(state='enriched', candidate=metrics(child),
                        capture_file=f'captures/{key}.json',
                        capture_json_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                        raw_unchanged=child['raw_response_base64'] == page['raw_response_base64'],
                        body_unchanged=child['content'] == page['content'],
                        original_capture_time=page.get('retrieved_at_utc') or page.get('retrieved_at'),
                        truth_verified=False, relevance_verified=False)
                except Exception as exc:
                    row.update(state='reparse_gap', error=type(exc).__name__, detail=str(exc)[:500])
                rows.append(row)
        report = {'schema': 'crawl4ai_saved_replay_report_v1', 'rows': rows,
            'eligible_pages': eligible, 'unprocessed_pages': eligible-len(rows),
            'enriched_pages': sum(r['state'] == 'enriched' for r in rows),
            'reparse_gaps': sum(r['state'] != 'enriched' for r in rows),
            'original_readable_pages': sum(r['original']['readable'] for r in rows),
            'candidate_readable_pages': sum(r.get('candidate', {}).get('readable', False) for r in rows),
            'readable_to_gap': [r['url'] for r in rows if r['original']['readable'] and
                r['state'] == 'enriched' and not r['candidate']['readable']],
            'raw_and_body_preserved': all(r.get('raw_unchanged') and r.get('body_unchanged')
                for r in rows if r['state'] == 'enriched'),
            'provider_calls': {'models': 0, 'tavily': 0, 'exa': 0, 'network': 0},
            'scope': 'Paired local reparse of identical saved bytes. No new recall or prediction-quality claim.'}
        save(root/'report.json', report)
        return report


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('catalog')
    collect = commands.add_parser('collect')
    collect.add_argument('--input', type=Path, required=True)
    collect.add_argument('--root', type=Path, required=True)
    collect.add_argument('--backend', choices=['crawl4ai', 'release_playwright'], default='crawl4ai')
    collect.add_argument('--browser-channel', choices=['chromium', 'chrome', 'msedge'], default='chromium')
    reparse = commands.add_parser('reparse')
    reparse.add_argument('--input', type=Path, required=True)
    reparse.add_argument('--output', type=Path, required=True)
    reparse.add_argument('--query', default='')
    pipeline = commands.add_parser('pipeline')
    pipeline.add_argument('--input', type=Path, required=True)
    pipeline.add_argument('--root', type=Path, required=True)
    pipeline.add_argument('--execute', action='store_true')
    pipeline.add_argument('--supplement-network', action='store_true')
    replay = commands.add_parser('replay')
    replay.add_argument('--input', type=Path, nargs='+', required=True)
    replay.add_argument('--root', type=Path, required=True)
    replay.add_argument('--max-pages', type=int, default=100)
    args = parser.parse_args()
    if args.command == 'catalog':
        result = capability_catalog()
    elif args.command == 'reparse':
        result = enrich_html(json.loads(args.input.read_text(encoding='utf-8')), query=args.query)
        save(args.output, result)
        result = metrics(result)
    elif args.command == 'collect':
        result = collect_sources(json.loads(args.input.read_text(encoding='utf-8')), args.root, backend=args.backend, browser_channel=args.browser_channel)
    elif args.command == 'replay':
        result = replay_saved_packages(args.input, args.root, max_pages=args.max_pages)
    else:
        request = json.loads(args.input.read_text(encoding='utf-8'))
        if args.execute:
            result = run_pipeline(request, args.root, supplement_network=args.supplement_network)
        else:
            from ForecastAgent.acquisition.pipeline import identity
            result = {'preflight': identity(pipeline_request(request), args.supplement_network), 'nextgen': capability_catalog(), 'model_calls': 0}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
