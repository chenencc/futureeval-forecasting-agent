"""Bounded public-page rendering; no user profiles, logins or model calls."""
import time
from ForecastAgent.providers.ultra import public_url
from ForecastAgent.readers.loader import load_response


class BrowserCaptureError(RuntimeError):
    def __init__(self, message, audit):
        super().__init__(message)
        self.audit = audit


def render_page(url, *, retrieved_at, request_limit=25, timeout_ms=20000, _public_check=public_url):
    from playwright.sync_api import sync_playwright
    if not _public_check(url):
        raise ValueError('Not a public URL')
    audit = {'requests':[], 'request_limit':request_limit, 'elapsed_seconds':None}
    started = time.monotonic()
    try:
        with sync_playwright() as api:
            browser = api.chromium.launch(headless=True)
            try:
                context = browser.new_context(service_workers='block', accept_downloads=False)
                page = context.new_page()
                deadline = started + timeout_ms / 1000

                def route_request(route):
                    request = route.request
                    reason = None
                    if request.method not in {'GET','HEAD'}:
                        reason = 'non_read_method'
                    elif request.resource_type in {'image','media','font'}:
                        reason = 'unnecessary_resource'
                    elif sum(r['allowed'] for r in audit['requests']) >= request_limit:
                        reason = 'request_budget'
                    elif time.monotonic() >= deadline:
                        reason = 'deadline'
                    elif not _public_check(request.url):
                        reason = 'non_public_destination'
                    audit['requests'].append({'url':request.url, 'method':request.method,
                        'resource_type':request.resource_type, 'allowed':reason is None, 'reason':reason})
                    route.abort() if reason else route.continue_()

                context.route('**/*',route_request)
                response = page.goto(url,wait_until='domcontentloaded',timeout=timeout_ms)
                if response is None or response.status >= 400:
                    raise ValueError(f'Navigation HTTP {response.status if response else "unknown"}')
                remaining = max(1,int((deadline-time.monotonic())*1000))
                try:
                    page.wait_for_function("document.body && document.body.innerText.trim().length >= 250",
                        timeout=min(5000,remaining))
                except Exception as exc:
                    audit['wait_warning'] = type(exc).__name__
                html = page.content().encode('utf-8')
                if len(html) > 3_000_000:
                    raise ValueError('Rendered DOM exceeds byte limit')
                snapshot = load_response({'url':url,'final_url':page.url,'raw':html,
                    'content_type':'text/html','charset':'utf-8','response_headers':{}},
                    retrieved_at=retrieved_at,preserve_raw_on_failure=True)
                snapshot.update(capture_method='browser_render',raw_kind='rendered_dom_utf8',
                    dom_sha256=snapshot['sha256'],navigation_http_status=response.status,
                    temporal_status='current_render_not_historical_proof')
                audit['elapsed_seconds'] = time.monotonic()-started
                snapshot['browser_audit'] = audit
                return snapshot
            finally:
                browser.close()
    except Exception as exc:
        audit['elapsed_seconds'] = time.monotonic()-started
        raise BrowserCaptureError(str(exc),audit) from exc
