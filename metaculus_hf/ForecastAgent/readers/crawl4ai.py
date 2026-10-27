"""Optional Crawl4AI adapter with saved DOM, bounded reads and no model calls."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import re
import time
from urllib.parse import urlsplit

from ForecastAgent.providers.ultra import public_url
from ForecastAgent.readers.browser import BrowserCaptureError
from ForecastAgent.readers.loader import load_response
from ForecastAgent.readers.quality import body_diagnostics

BACKEND_VERSION = '0.9.4'
ADAPTER_VERSION = 'crawl4ai_capture_v1'
MAX_DOM_BYTES = 3_000_000
MAX_SAVED_HTML_BYTES = 8_000_000
MAX_TABLE_CHARS = 150_000


def require_backend():
    actual = version('crawl4ai')
    if actual != BACKEND_VERSION:
        raise ValueError(f'Expected Crawl4AI {BACKEND_VERSION}; found {actual}')


def enrich_html(snapshot, *, query='', max_chars=150_000):
    """Reparse verified HTML without a browser, network, LLM or content selection."""
    require_backend()
    if snapshot.get('content_type') not in {'text/html', 'application/xhtml+xml'}:
        raise ValueError('Use existing PDF and structured-data readers for other formats')
    raw = base64.b64decode(snapshot['raw_response_base64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != snapshot['sha256']:
        raise ValueError('Saved HTML hash mismatch')
    if len(raw) > MAX_SAVED_HTML_BYTES:
        raise ValueError('Saved HTML exceeds adapter byte limit')
    from ForecastAgent.readers.encoding import decode_response
    from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
    from crawl4ai.content_filter_strategy import BM25ContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.table_extraction import DefaultTableExtraction
    from crawl4ai.antibot_detector import is_blocked
    decoded, _ = decode_response(raw, snapshot.get('response_headers'))
    if len(decoded) > MAX_SAVED_HTML_BYTES:
        raise ValueError('Decoded HTML exceeds adapter byte limit')
    html = decoded.decode(snapshot.get('charset') or 'utf-8', errors='replace')
    # Upstream expands header colspans before applying its body-grid limits.
    # Reject an excessive expansion instead of silently changing source cells.
    from lxml import html as lxml_html
    tree = lxml_html.fromstring(html)
    for value in tree.xpath('//table//th/@colspan | //table//td/@colspan'):
        if str(value).isdigit() and int(value) > 1000:
            raise ValueError('Table header span exceeds supported grid bounds')
    if any(len(table.xpath('.//tr')) > 5000 for table in tree.xpath('//table')):
        raise ValueError('Oversized HTML table requires the structured-data reader')
    blocked, block_reason = is_blocked(status_code=snapshot.get('navigation_http_status') or 200, html=html)
    source = snapshot.get('final_url') or snapshot['url']
    parsed = LXMLWebScrapingStrategy().scrap(
        source, html, word_count_threshold=0, table_score_threshold=0,
        table_extraction=DefaultTableExtraction(table_score_threshold=0),
        exclude_all_images=True, image_description_min_word_threshold=1_000_000,
        process_iframes=False, remove_forms=False, excluded_tags=['script', 'style'])
    cleaned = parsed.cleaned_html
    # Fit Markdown is a reading projection. It never replaces archived material.
    generator = DefaultMarkdownGenerator(
        content_filter=BM25ContentFilter(user_query=query, bm25_threshold=1.0) if query else None)
    markdown = generator.generate_markdown(cleaned, base_url=source)
    fields = ('raw_markdown', 'markdown_with_citations', 'references_markdown', 'fit_markdown')
    extras = {field: (getattr(markdown, field, None) or '')[:max_chars] for field in fields}
    extras['markdown_truncated'] = any(len(getattr(markdown, field, None) or '') > max_chars for field in fields)
    extras.update(version=BACKEND_VERSION, adapter_version=ADAPTER_VERSION,
                  processed_at_utc=datetime.now(timezone.utc).isoformat(),
                  source_sha256=snapshot['sha256'], cleaned_html_sha256=hashlib.sha256(cleaned.encode()).hexdigest(),
                  filtered_content_is_optional_projection=True, model_calls=0, network_calls=0)
    if extras['raw_markdown']:
        # Readers can inspect this projection without losing the legacy body.
        markdown_document = {'page_content': extras['raw_markdown'], 'metadata': {
            'source': source, 'format': 'html_markdown', 'source_sha256': snapshot['sha256'],
            'extraction_engine': ADAPTER_VERSION, 'truncated': extras['markdown_truncated']}}
    else:
        markdown_document = None
    extras['upstream_block_diagnostic'] = {'detected': blocked, 'reason': block_reason,
        'scope': 'Diagnostic only: upstream broad heuristics do not independently reject short official notices.'}
    # Tables live in ScrapingResult.media in v0.9.4, rather than a tables field.
    media = parsed.media.model_dump() if hasattr(parsed.media, 'model_dump') else (parsed.media or {})
    tables = media.get('tables', [])
    documents = [document for document in snapshot.get('documents', [])
                 if document.get('metadata', {}).get('extraction_engine') != ADAPTER_VERSION]
    if markdown_document:
        documents.append(markdown_document)
    structured = []
    remaining = MAX_TABLE_CHARS
    tables_truncated = False
    for index, table in enumerate(tables, 1):
        if index > 30 or remaining <= 0:
            tables_truncated = True
            break
        headers = [str(cell) for cell in table.get('headers', [])]
        rows = []
        for row in table.get('rows', []):
            cells = [str(cell) for cell in row]
            cost = sum(len(cell) for cell in cells) + len(cells) * 3
            if len(rows) >= 2000 or cost > remaining:
                tables_truncated = True
                break
            rows.append(cells)
            remaining -= cost
        clipped = len(rows) != len(table.get('rows', []))
        structured.append({'table': index, 'headers': headers, 'rows': rows,
                           'caption': str(table.get('caption') or '')[:2000],
                           'original_row_count': len(table.get('rows', [])), 'truncated': clipped,
                           'source_sha256': snapshot['sha256'], 'parser': ADAPTER_VERSION})
        value = '\n'.join(' | '.join(row) for row in ([headers] if headers else []) + rows)
        if value:
            documents.append({'page_content': value, 'metadata': {
                'source': source, 'format': 'html_table', 'table': index,
                'extraction_engine': ADAPTER_VERSION, 'source_sha256': snapshot['sha256'],
                'row_count': len(rows), 'truncated': clipped,
                'table_warning': 'Parsed cell grid; inspect the saved HTML for nested or ambiguous headers.'}})
    dom_table_count = snapshot.get('page_date_metadata', {}).get('html_table_count')
    extras.update(structured_tables=structured, tables_truncated=tables_truncated,
                  parsed_table_count=len(tables), dom_table_count=dom_table_count,
                  table_inventory_complete=dom_table_count == len(tables) and not tables_truncated)
    link_rows = []
    links = parsed.links.model_dump() if hasattr(parsed.links, 'model_dump') else (parsed.links or {})
    for role in ('internal', 'external'):
        for item in links.get(role, []):
            link_rows.append({'url': item.get('href', ''), 'text': str(item.get('text') or '')[:500], 'role': role})
    extras['links_truncated'] = len(link_rows) > 1000
    extras['observed_links'] = link_rows[:1000]
    from ForecastAgent.readers.material_structure import discover_resources, source_sections
    structure = source_sections(snapshot)
    result = dict(snapshot, crawl4ai=extras, documents=documents, source_structure=structure)
    result['embedded_resources'] = discover_resources(snapshot)
    result['documents_truncated'] = bool(snapshot.get('documents_truncated')) or tables_truncated
    result['links'] = list(dict.fromkeys(list(snapshot.get('links', [])) + [r['url'] for r in link_rows[:1000] if r['url']]))
    # Judge the unfiltered body: a filter must never hide a challenge or login.
    diagnostic = body_diagnostics(snapshot.get('content', ''), metadata=snapshot.get('page_date_metadata'))
    if len(snapshot.get('content', '')) < 1500 and re.search(
            r'(?:verifying|checking)\s+your\s+browser\s+(?:before|to)|browser\s+verification.*?incident\s+id',
            snapshot.get('content', ''), re.I | re.S):
        diagnostic.update(state='access_interstitial', usable_text=False)
    code = snapshot.get('navigation_http_status')
    observed = {'schema': 'nextgen_capture_status_v1', 'body_state': diagnostic['state'],
                'usable_text': diagnostic['usable_text'], 'raw_state': 'verified',
                'category': 'readable_body' if diagnostic['usable_text'] else 'body_gap',
                'http_status': code, 'transport_state': 'http_success' if isinstance(code, int) else 'not_recorded',
                'truth_verified': False, 'relevance_verified': False}
    if isinstance(code, int) and code >= 400:
        observed.update(usable_text=False, category='http_error', transport_state='http_error')
    result['capture_status'] = observed
    result['body_diagnostics'] = {**diagnostic, 'usable_text': observed['usable_text'], 'state': observed['body_state']}
    return result


class RequestGuard:
    """Count routed dependencies before dispatch, including failed reads."""
    def __init__(self, limit, deadline, public_check, validated_url=None):
        self.limit = limit
        self.deadline = deadline
        self.public_check = public_check
        self.allowed = 0
        self.total = 0
        self.rows = []
        self.validated_origins = set()
        self.destination_checks = 0
        self.reused_destination_checks = 0
        if validated_url:
            self.validated_origins.add(self.origin(validated_url))

    @staticmethod
    def origin(url):
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            raise ValueError('Unsupported destination')
        return parts.scheme, parts.hostname, parts.port

    async def route(self, route):
        request = route.request
        self.total += 1
        reason = None
        if request.method not in {'GET', 'HEAD'}:
            reason = 'non_read_method'
        elif request.resource_type in {'image', 'media', 'font'}:
            reason = 'unnecessary_resource'
        elif self.allowed >= self.limit:
            reason = 'request_budget'
        elif time.monotonic() >= self.deadline:
            reason = 'deadline'
        else:
            try:
                origin = self.origin(request.url)
                if origin in self.validated_origins:
                    self.reused_destination_checks += 1
                elif not await asyncio.wait_for(asyncio.to_thread(self.public_check, request.url), timeout=3):
                    reason = 'non_public_destination'
                else:
                    self.destination_checks += 1
                    self.validated_origins.add(origin)
            except (TimeoutError, OSError, ValueError):
                reason = 'destination_check_failed'
        # The final gate closes races between concurrent destination checks.
        if reason is None and (self.allowed >= self.limit or time.monotonic() >= self.deadline):
            reason = 'request_budget' if self.allowed >= self.limit else 'deadline'
        if reason is None:
            self.allowed += 1
        if len(self.rows) < 500:
            self.rows.append({'url': request.url, 'method': request.method,
                              'resource_type': request.resource_type, 'allowed': reason is None, 'reason': reason})
        await route.abort() if reason else await route.continue_()

    def audit(self):
        return {'requests': self.rows, 'request_limit': self.limit, 'allowed_requests': self.allowed,
                'observed_requests': self.total, 'request_log_truncated': self.total > len(self.rows),
                'destination_checks': self.destination_checks,
                'reused_destination_checks': self.reused_destination_checks,
                'request_accounting_scope': 'Application-routed dispatches, not a network transaction or redirect-hop count.'}


async def render_page_async(url, *, retrieved_at, request_limit=25, timeout_ms=20000,
                            query='', wait_for_css=None, scroll=False,
                            browser_channel='chromium', _public_check=public_url):
    require_backend()
    if not 1 <= request_limit <= 100 or not 1000 <= timeout_ms <= 60000:
        raise ValueError('Browser bounds are outside supported limits')
    if browser_channel not in {'chromium', 'chrome', 'msedge'}:
        raise ValueError('Unsupported browser channel')
    if wait_for_css is not None and (not isinstance(wait_for_css, str) or not 1 <= len(wait_for_css) <= 200):
        raise ValueError('A bounded CSS selector is required')
    if not await asyncio.wait_for(asyncio.to_thread(_public_check, url), timeout=3):
        raise ValueError('Not a public URL')
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    started = time.monotonic()
    guard = RequestGuard(request_limit, started + timeout_ms / 1000, _public_check, validated_url=url)
    from ForecastAgent.readers.network_data import DataResponseObserver
    data_observer = DataResponseObserver(url, guard)
    audit = {'backend': 'crawl4ai', 'backend_version': BACKEND_VERSION, 'cache_mode': 'DISABLED',
             'browser_channel': browser_channel,
             'model_calls': 0, 'automatic_retries': 0, 'elapsed_seconds': None}
    crawler = AsyncWebCrawler(config=BrowserConfig(
        headless=True, verbose=False, accept_downloads=False, enable_stealth=False, chrome_channel=browser_channel,
        use_persistent_context=False, extra_args=['--disable-dev-shm-usage']), verbose=False)
    pages = []

    async def page_created(page, context, **kwargs):
        pages.append(page)
        def observed_response(response):
            if response.request.is_navigation_request() and response.request.frame == page.main_frame:
                audit['navigation_http_status'] = response.status
                audit['final_url'] = response.url
        page.on('response', observed_response)
        page.on('response', data_observer.observe)
        await context.route('**/*', guard.route)
        def close_socket(socket):
            audit.setdefault('transport_gaps', [])
            if len(audit['transport_gaps']) < 10:
                audit['transport_gaps'].append({'transport': 'websocket', 'reason': 'Disabled by read-only capture policy'})
            socket.close()
        await context.route_web_socket('**/*', close_socket)
        # No service workers can make unaccounted background requests.
        await page.add_init_script("if ('serviceWorker' in navigator) {navigator.serviceWorker.register = () => Promise.reject(new Error('Disabled in acquisition'));}")
        return page

    async def after_goto(page, response=None, **kwargs):
        audit['navigation_http_status'] = response.status if response else None
        audit['final_url'] = page.url
        if wait_for_css:
            try:
                await page.wait_for_selector(wait_for_css, timeout=min(5000, timeout_ms // 2))
            except Exception as exc:
                audit['wait_warning'] = type(exc).__name__
        return page

    crawler.crawler_strategy.set_hook('on_page_context_created', page_created)
    crawler.crawler_strategy.set_hook('after_goto', after_goto)
    config = CrawlerRunConfig(cache_mode=CacheMode.DISABLED, verbose=False, word_count_threshold=0,
        max_retries=0, page_timeout=timeout_ms, wait_until='domcontentloaded',
        delay_before_return_html=1.5, scan_full_page=bool(scroll), max_scroll_steps=4,
        process_iframes=False, capture_network_requests=False,
        exclude_all_images=True, table_score_threshold=0)

    def snapshot_from_dom(html, final_url, *, partial=False, crawler_success=False):
        if not html or len(html) > MAX_DOM_BYTES:
            raise ValueError('Missing or oversized rendered DOM')
        snapshot = load_response({'url': url, 'final_url': final_url, 'raw': html,
            'content_type': 'text/html', 'charset': 'utf-8', 'response_headers': {}},
            retrieved_at=retrieved_at, preserve_raw_on_failure=True)
        snapshot.update(capture_method='crawl4ai_browser_render', raw_kind='rendered_dom_utf8',
            dom_sha256=snapshot['sha256'], navigation_http_status=audit.get('navigation_http_status'),
            temporal_status='current_render_not_historical_proof', crawler_reported_success=crawler_success,
            render_state='deadline_partial_dom' if partial else 'completed')
        snapshot = enrich_html(snapshot, query=query)
        if partial:
            snapshot['render_gaps'] = ['Render deadline reached; late or dependent content may be missing.']
            snapshot['capture_status']['render_complete'] = False
            if snapshot['capture_status']['usable_text']:
                snapshot['capture_status']['category'] = 'partial_body'
        else:
            snapshot['capture_status']['render_complete'] = True
        audit.update(guard.audit(), elapsed_seconds=round(time.monotonic() - started, 3))
        snapshot['browser_audit'] = audit
        snapshot['data_response_capture'] = data_observer.export()
        from ForecastAgent.readers.material_structure import discover_resources
        snapshot['embedded_resources'] = discover_resources(snapshot)
        return snapshot

    try:
        async def execute():
            await crawler.start()
            return await crawler.arun(url=url, config=config)
        captured = await asyncio.wait_for(execute(), timeout=timeout_ms / 1000)
        await data_observer.finish()
        # arun() returns a CrawlResultContainer even for a single URL in 0.9.4.
        result = captured[0] if hasattr(captured, '__getitem__') else captured
        html = (result.html or '').encode('utf-8')
        if not html:
            raise ValueError(result.error_message or 'No rendered DOM returned')
        if len(html) > MAX_DOM_BYTES:
            raise ValueError('Rendered DOM exceeds adapter byte limit')
        final_url = audit.get('final_url') or result.redirected_url or url
        if not await asyncio.wait_for(asyncio.to_thread(_public_check, final_url), timeout=2):
            raise ValueError('Final destination is not public')
        return snapshot_from_dom(html, final_url, crawler_success=bool(result.success))
    except Exception as exc:
        await data_observer.finish()
        audit['failure_type'] = type(exc).__name__
        # No new navigation or dispatch: rescue only the DOM already in memory.
        if isinstance(exc, TimeoutError) and pages:
            try:
                page = pages[-1]
                final_url = page.url
                if guard.origin(final_url) in guard.validated_origins:
                    html = (await asyncio.wait_for(page.content(), timeout=1)).encode('utf-8')
                    return snapshot_from_dom(html, final_url, partial=True)
            except Exception as rescue_error:
                audit['partial_dom_gap'] = type(rescue_error).__name__
        audit.update(guard.audit(), elapsed_seconds=round(time.monotonic() - started, 3))
        # Completed data captures survive even when no usable parent DOM exists.
        audit['data_response_capture'] = data_observer.export()
        raise BrowserCaptureError(str(exc) or type(exc).__name__, audit) from exc
    finally:
        try:
            await asyncio.wait_for(crawler.close(), timeout=5)
        except Exception:
            pass
        audit['elapsed_including_cleanup_seconds'] = round(time.monotonic() - started, 3)


def render_page(url, **kwargs):
    """Synchronous injection seam compatible with the existing supplement stage."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(render_page_async(url, **kwargs))
    raise RuntimeError('Use render_page_async inside an existing event loop')


def discover_links(snapshot, *, query='', limit=5):
    """Rank observed same-host details locally; never invent or fetch a URL."""
    terms = set(re.findall(r'[a-z0-9]{3,}', query.casefold()))
    host = urlsplit(snapshot.get('final_url') or snapshot['url']).hostname
    candidates = {}
    for row in snapshot.get('crawl4ai', {}).get('observed_links', []):
        url = row['url']
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or parts.hostname != host or parts.username or parts.password:
            continue
        if re.search(r'/(login|signin|account|privacy|terms)(/|$)', parts.path, re.I):
            continue
        overlap = terms & set(re.findall(r'[a-z0-9]{3,}', (url + ' ' + row['text']).casefold()))
        if not overlap:
            continue
        candidates[url] = {'url': url, 'anchor': row['text'], 'matched_terms': sorted(overlap),
                           'score': len(overlap), 'parent_url': snapshot['url'],
                           'parent_dom_sha256': snapshot['sha256'], 'relevance_verified': False}
    return sorted(candidates.values(), key=lambda r: (-r['score'], r['url']))[:min(max(limit, 0), 10)]
