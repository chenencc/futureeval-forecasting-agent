"""Independent, bounded original-body repair; historical captures remain immutable."""
import copy
import re
from pathlib import Path
from urllib.parse import urlsplit

from .common import digest, now, read, write

VERSION = 'external-original-recovery-v1'
# Additional repair reservations are independent of the native search ledger.
# They never renew searches, native captures, model calls, or task executions.
HTTP_CAP, BROWSER_CAP, TARGET = 4, 2, 2
MARKET_HOSTS = {'polymarket.com', 'polymarket.us', 'kalshi.com'}


def market_host(url):
    host = (urlsplit(url).hostname or '').lower()
    return any(host == h or host.endswith('.' + h) for h in MARKET_HOSTS)


def quality(page, url):
    from ForecastAgent.intelligence.admission import inspect
    diagnostic = inspect(page)
    text = re.sub(r'\s+', ' ', page.get('content', '')).strip().lower()
    # Short first-person access interstitials, not articles discussing restrictions.
    restricted = ('trading is blocked in the united states on' in text or
                  ('not available in your country' in text and len(text) < 1200) or
                  ('go to polymarket.us' in text and len(text) < 1200))
    if restricted:
        diagnostic.update(state='geographic_access_interstitial', usable_text=False)
    else:
        lines = [s.strip().lower() for s in page.get('content', '').splitlines() if s.strip()]
        controls = {'about','press','copyright','contact us','creators','advertise','developers',
                    'terms','privacy','policy & safety','how youtube works','test new features',
                    'nfl sunday ticket','home','search','more','sign in','log in','menu'}
        chrome = sum(s in controls or bool(re.fullmatch(r'©?\s*\d{4}\s+\w+(?:\s+\w+)*', s)) for s in lines)
        if len(lines) >= 5 and chrome / len(lines) >= .8 and len(text) < 1500:
            diagnostic.update(state='navigation_or_footer_only', usable_text=False)
    if not restricted and market_host(url):
        diagnostic.update(state='market_rules_or_signal_not_external_event_evidence', usable_text=False)
    return diagnostic


def prepare(bundle):
    from ForecastAgent.intelligence.admission import prepare as admit
    view, audit = admit(bundle)
    rejected = []
    for url, page in list(view.get('pages', {}).items()):
        diagnostic = quality(page, url)
        if not diagnostic['usable_text']:
            del view['pages'][url]
            rejected.append({'url': url, 'body_state': diagnostic['state'],
                             'body_sha256': digest(page.get('content', ''))})
    allowed = set(view['pages'])
    for field in ('evidence', 'excerpts'):
        view[field] = [e for e in view.get(field, []) if e.get('url') in allowed]
    view.setdefault('gaps', []).extend({'code': 'market_body_not_admitted', **r} for r in rejected)
    audit.update(market_body_exclusions=rejected, external_body_count=len(allowed),
                 body_readability_is_not_relevance_or_truth=True)
    return view, audit


def candidates(bundle):
    """Rank saved discoveries only. Ranking does not certify source truth."""
    sources = {}
    request = bundle.get('request', {})
    text = request.get('resolution_criteria', '')
    official = set(re.findall(r'https?://[^\s<>"\)]+', text)) | set(request.get('external_rule_sources', []))
    words = set(re.findall(r'\b[a-z]{4,}\b', request.get('question', '').lower()))
    def add(item):
        if not isinstance(item, dict): return
        url = item.get('url', '')
        if urlsplit(url).scheme != 'https' or market_host(url): return
        title = item.get('title', '')
        match = len(words & set(re.findall(r'\b[a-z]{4,}\b', title.lower())))
        host = (urlsplit(url).hostname or '').lower()
        score = (url in official, host.endswith('.gov') or host.endswith('.gov.br'),
                 match, str(item.get('published_date') or item.get('publishedDate') or ''))
        if url not in sources or score > sources[url]['rank']:
            sources[url] = {'url': url, 'title': title, 'rank': score, 'published_date': item.get('published_date')}
    leads = bundle.get('source_leads', {})
    for item in (leads.values() if isinstance(leads, dict) else leads): add(item)
    for search in bundle.get('searches', []) + bundle.get('exa_searches', []):
        for item in search.get('results', []): add(item)
    for url in official: add({'url': url})
    return sorted(sources.values(), key=lambda s: s['rank'], reverse=True)


def recover(bundle, directory, capture=None):
    """Four HTTP and two browser attempts, persisted before each child starts."""
    from ForecastAgent.supplement.transport import capture as native_capture
    capture = capture or native_capture
    root = Path(directory).resolve()
    identity = {'version': VERSION, 'parent_sha256': digest(bundle), 'http_cap': HTTP_CAP,
                'browser_cap': BROWSER_CAP, 'target_bodies': TARGET, 'new_search_cap': 0}
    if (root / 'identity.json').exists() and read(root / 'identity.json') != identity:
        raise ValueError('Original repair identity changed')
    write(root / 'identity.json', identity)
    if (root / 'result.json').exists():
        report, child = read(root / 'result.json'), read(root / 'bundle.json')
        if digest(child) != report['bundle_sha256']: raise ValueError('Repaired body bytes changed')
        return child, report
    records_path = root / 'captures.json'
    records = read(records_path) if records_path.exists() else []
    child = copy.deepcopy(bundle)
    for r in records:
        if r['status'] == 'completed': child.setdefault('pages', {})[r['url']] = read(root / r['page_file'])
    leads = candidates(bundle)
    write(root / 'saved-leads.json', leads)
    for method, cap in (('http', HTTP_CAP), ('browser', BROWSER_CAP)):
        for lead in leads:
            view, _ = prepare(child)
            if len(view['pages']) >= TARGET: break
            url = lead['url']
            if url in view['pages'] or any(r['url'] == url and r['method'] == method for r in records): continue
            if sum(r['method'] == method for r in records) >= cap: break
            # Browser recovery is for failed/empty HTTP originals, not fresh URLs.
            if method == 'browser' and not any(r['url'] == url and r['method'] == 'http' for r in records): continue
            index = len(records) + 1
            row = {'method': method, 'url': url, 'reserved_at': now(), 'status': 'reserved_unknown'}
            records.append(row); write(records_path, records)
            try:
                page = capture(method, url, root / ('capture-' + str(index)), timeout_seconds=60,
                               browser_profile={'backend': 'crawl4ai', 'query': ''} if method == 'browser' else None)
                page.setdefault('retrieved_at_utc', now())
                diagnostic = quality(page, url)
                filename = 'page-' + str(index) + '.json'; write(root / filename, page)
                row.update(status='completed', page_file=filename, diagnostic=diagnostic,
                           captured_at=page['retrieved_at_utc'], body_sha256=digest(page.get('content', '')))
                child.setdefault('pages', {})[url] = page
            except Exception as exc:
                row.update(status='failed', error_type=type(exc).__name__)
            finally:
                write(records_path, records)
    view, audit = prepare(child)
    child.setdefault('gaps', []).extend({'code': 'body_recovery_failed', 'url': r['url'],
        'method': r['method'], 'status': r['status']} for r in records if r['status'] != 'completed')
    child['market_original_recovery'] = {'version': VERSION, 'parent_sha256': digest(bundle),
        'captured_at': now(), 'no_new_searches': True, 'budget_reset': False}
    report = {'version': VERSION, 'external_body_count': len(view['pages']), 'admission': audit,
              'physical_capture_reservations': len(records), 'new_searches': 0,
              'unattempted_leads': len({s['url'] for s in leads} - {r['url'] for r in records}),
              'budget_reset': False, 'parent_sha256': digest(bundle), 'bundle_sha256': digest(child)}
    write(root / 'bundle.json', child); write(root / 'result.json', report)
    return child, report
