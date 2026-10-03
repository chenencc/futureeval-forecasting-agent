"""Observed URL extraction and evidence identity without rewriting fetch queries."""
import hashlib
import re
import base64
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, urljoin


def observed_urls(text):
    text = re.sub(r'\\([&?_#=])', r'\1', unescape(str(text)))
    urls = []
    for match in re.finditer(r'https?://', text):
        position = match.start(); end = position; balance = 0
        while end < len(text):
            char = text[end]
            if char.isspace() or char in '<>"\'[]':
                break
            if char == '(':
                balance += 1
            elif char == ')':
                if not balance:
                    break
                balance -= 1
            end += 1
        url = text[position:end].rstrip('.,;!')
        if url and url not in urls:
            urls.append(url)
    return urls


def transport_key(url):
    """Exact resource key: preserve path case, trailing slash and query bytes."""
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def observed_links(text):
    decoded = re.sub(r'\\([&?_#=])', r'\1', unescape(str(text)))
    result = []
    for url in observed_urls(decoded):
        start = decoded.find(url)
        label = re.search(r'\[([^\]\n]*)\]\(\s*$',decoded[:start]) if start >= 0 else None
        result.append({'url':url,'label':label[1] if label else ''})
    return result


def page_links(page, parent):
    """Normalize saved string/dict links and recover labels from verified HTML.

    Only recorded hrefs or links in the saved body enter discovery. No URL
    templates, network requests, HTML execution or parent identity claims.
    """
    links = {}
    def add(url, label='', origin='saved_links'):
        if not isinstance(url, str) or not url.strip(): return
        url = urljoin(page.get('final_url') or parent, url)
        if urlsplit(url).scheme not in {'http', 'https'}: return
        key = transport_key(url)
        row = links.setdefault(key, {'url':url, 'label':'', 'observed_in':origin})
        if label and len(label) > len(row['label']): row['label'] = label[:500]
    for link in page.get('links', []):
        if isinstance(link, str): add(link)
        elif isinstance(link, dict): add(link.get('url') or link.get('href'), link.get('text') or link.get('label',''))
    for link in observed_links(page.get('content', '')):
        add(link['url'], link['label'], 'saved_body')
    raw = page.get('raw_response_base64')
    if raw and len(raw) <= 4_000_000:
        try:
            payload = base64.b64decode(raw, validate=True)
            if hashlib.sha256(payload).hexdigest() == page.get('sha256'):
                html = payload.decode(page.get('charset') or 'utf-8',errors='replace')
                if not page.get('links') and ('<a ' in html.lower()):
                    from ForecastAgent.readers.html import ReadableHTML
                    parser=ReadableHTML(); parser.feed(html)
                    for href in parser.links: add(href,origin='verified_saved_html')
                class Labels(HTMLParser):
                    def __init__(self):
                        super().__init__(convert_charrefs=True); self.href=None; self.parts=[]
                    def handle_starttag(self, tag, attrs):
                        if tag == 'a': self.href=dict(attrs).get('href'); self.parts=[]
                    def handle_data(self, data):
                        if self.href: self.parts.append(data)
                    def handle_endtag(self, tag):
                        if tag == 'a' and self.href:
                            # Existing readers exclude nav/footer links. Recover
                            # labels only for their recorded links, not all DOM hrefs.
                            if transport_key(urljoin(page.get('final_url') or parent,self.href)) in links:
                                add(self.href, ' '.join(' '.join(self.parts).split()), 'verified_saved_html')
                            self.href=None
                Labels().feed(html)
        except (ValueError, TypeError, LookupError): pass
    return list(links.values())


def alias_family(url):
    """Possible slash alias only; never claims equivalence or rewrites transport."""
    p = urlsplit(transport_key(url))
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip('/') or '/', p.query, ''))


def inventory(pages):
    groups = {}; families = {}
    for url, page in pages.items():
        body = page.get('content', '')
        calculated = hashlib.sha256(body.encode()).hexdigest()
        if page.get('content_sha256') and page['content_sha256'] != calculated:
            raise ValueError('Saved parsed body hash changed')
        groups.setdefault(calculated, []).append(url)
        families.setdefault(alias_family(url), []).append(url)
    duplicates = [dict(content_sha256=key, urls=urls, unique_body_count=1)
                  for key, urls in groups.items() if len(urls) > 1]
    return {'capture_count': len(pages), 'unique_body_count': len(groups),
        'identical_body_groups': duplicates,
        'possible_url_aliases': [urls for urls in families.values() if len(urls) > 1],
        'all_originals_preserved': True, 'resource_equivalence_verified': False}
