"""One metadata-only Exa search; no retries or paid content extraction."""
import json
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ForecastAgent.providers.tavily_search import canonical_url, search_options

ENDPOINT = 'https://api.exa.ai/search'
CATEGORIES = {'general', 'news', 'publication', 'financial report'}


class ExaError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f'Exa HTTP {status}; attempt consumed, no automatic retry')
        self.http_status = status
        self.response_body = body


def options(query, category='general', include_domains=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 350:
        raise ValueError('Exa query requires 1-350 characters')
    if category not in CATEGORIES:
        raise ValueError('Unsupported Exa category')
    domains = search_options(query, include_domains=include_domains,
                             include_domains_mode='restrict' if include_domains else 'prefer')['include_domains']
    return {'category': category, 'include_domains': domains, 'type': 'auto', 'numResults': 10}


def search(query, api_key, *, category='general', include_domains=None, cutoff=None,
           start=None, exclude_urls=()):
    selected = options(query, category, include_domains)
    if not api_key:
        raise ValueError('EXA_API_KEY is not configured')
    payload = {'query': query, 'type': 'auto', 'numResults': 10,
               'excludeDomains': ['metaculus.com']}
    if category != 'general':
        payload['category'] = category
    if selected['include_domains']:
        payload['includeDomains'] = selected['include_domains']
    for field, value in [('endPublishedDate', cutoff), ('startPublishedDate', start)]:
        if value:
            if value.tzinfo is None:
                raise ValueError('Exa date boundaries require a timezone')
            payload[field] = value.astimezone(timezone.utc).isoformat()
    # Omitting contents requests discovery metadata only. No summaries or deep modes.
    request = Request(ENDPOINT, data=json.dumps(payload).encode(),
                      headers={'x-api-key': api_key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Exa response exceeded bounded size')
        decoded = json.loads(raw)
    except HTTPError as exc:
        body = exc.read(8000).decode('utf-8', errors='replace').replace(api_key, '[REDACTED]')
        raise ExaError(exc.code, body) from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get('results'), list):
        raise ValueError('Invalid Exa response shape')
    seen = {canonical_url(url) for url in exclude_urls}
    results = []
    for item in decoded['results']:
        url = item.get('url') or ''
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        key = canonical_url(url)
        if parts.scheme not in {'http','https'} or parts.username or parts.password or not host:
            continue
        if key in seen or host == 'metaculus.com' or host.endswith('.metaculus.com'):
            continue
        if selected['include_domains'] and not any(host == d or host.endswith('.'+d) for d in selected['include_domains']):
            continue
        seen.add(key)
        results.append({'url':url, 'title':item.get('title') or 'Untitled source',
                        'published_date':item.get('publishedDate'), 'author':item.get('author'),
                        'provider':'exa', 'content':''})
        if len(results) == 10:
            break
    return {'results': results, 'raw_response': decoded, 'request_payload':payload,
            'request_id':decoded.get('requestId'), 'cost_dollars_estimate':decoded.get('costDollars'),
            'searched_at':datetime.now(timezone.utc).isoformat()}
