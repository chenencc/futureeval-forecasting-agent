"""Bounded RSS/Atom entries with publisher dates and real source links."""
from xml.etree import ElementTree
from urllib.parse import urljoin
from ForecastAgent.evidence.document import Document
from ForecastAgent.readers.html import parse_html


def parse_feed(raw, source):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('DTD/entity declarations are unsupported in feed snapshots')
    root = ElementTree.fromstring(raw)
    local = lambda name: name.rsplit('}', 1)[-1]
    if local(root.tag) not in {'rss', 'feed', 'RDF'}:
        raise ValueError('Only RSS or Atom feed XML is supported')
    documents = []; links = []; total = 0
    for item in root.iter():
        if local(item.tag) not in {'item', 'entry'}:
            continue
        total += 1
        if total > 1000:
            continue
        fields = {}; entry_links = []
        for child in item:
            name = local(child.tag)
            value = ''.join(child.itertext()).strip()
            if name == 'link':
                href = child.get('href') or value
                if href and child.get('rel', 'alternate') == 'alternate':
                    entry_links.append(urljoin(source, href))
            elif name in {'title', 'description', 'summary', 'content', 'pubDate', 'published', 'updated', 'date'} and value:
                fields[name] = parse_html(value, source)[0] if name in {'description', 'summary', 'content'} else value
        content = '\n'.join(f'{key}: {value}' for key, value in fields.items())
        if not content.strip():
            continue
        links.extend(entry_links)
        documents.append(Document(content, {'source': source, 'format': 'feed_entry', 'entry': total,
            'title': fields.get('title'), 'published_at': fields.get('published') or fields.get('pubDate') or fields.get('date'),
            'updated_at': fields.get('updated'), 'links': entry_links}))
    if not documents:
        raise ValueError('Feed snapshot contained no readable entries')
    return documents, list(dict.fromkeys(links)), total > 1000
