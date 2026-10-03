"""Observed URL extraction and evidence identity without rewriting fetch queries."""
import hashlib
import re
from html import unescape
from urllib.parse import urlsplit, urlunsplit


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
