from html.parser import HTMLParser
from urllib.parse import urljoin
import json
import re
from importlib.metadata import version
from ForecastAgent.providers.tavily_search import canonical_url

class ReadableHTML(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.links = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a" and not self.skip_depth:
            href = dict(attrs).get("href")
            if href and len(self.links) < 200:
                self.links.append(href)
        if tag in self.SKIP:
            self.skip_depth += 1
        if not self.skip_depth and tag in {"p", "li", "tr", "td", "th", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if not self.skip_depth and tag in {"p", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


def parse_html(text, source):
    metadata = {}
    class Dates(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "meta":
                label = (values.get("property") or values.get("name") or values.get("itemprop") or "").lower()
                if label in {"article:published_time", "datepublished", "date", "pubdate"}:
                    metadata["published_at"] = values.get("content")
                if label in {"article:modified_time", "datemodified", "last-modified"}:
                    metadata["updated_at"] = values.get("content")
    Dates().feed(text)
    parser = ReadableHTML(); parser.feed(text)
    links = list(dict.fromkeys(urljoin(source, href) for href in parser.links if canonical_url(urljoin(source, href))))
    fallback = "".join(parser.parts)
    metadata['extraction_engine'] = 'stdlib_html_fallback'
    try:
        import trafilatura
        extracted = trafilatura.extract(text, url=source, output_format='json', with_metadata=True,
                                        include_tables=True, include_links=True, include_comments=False)
        data = json.loads(extracted) if extracted else {}
        body = data.get('text') or ''
        if len(body.strip()) >= 80:
            fallback = body
            metadata.update(extraction_engine='trafilatura', extraction_version=version('trafilatura'))
            for name in ('title', 'author', 'date', 'sitename', 'url'):
                if data.get(name):
                    metadata['extracted_' + name] = data[name]
    except ImportError:
        metadata['parser_warning'] = 'Optional main-text extractor unavailable; using basic HTML parsing.'
    except (ValueError, TypeError) as exc:
        metadata['parser_warning'] = 'Main-text extraction failed: ' + type(exc).__name__
    metadata['headings'] = [re.sub('<[^>]+>', '', s).strip() for s in
                            re.findall(r'<h[1-6]\b[^>]*>(.*?)</h[1-6]>', text, re.I | re.S)][:100]
    metadata['html_table_count'] = len(re.findall(r'<table\b', text, re.I))
    metadata['tables'] = []
    try:
        from lxml import html
        tree=html.fromstring(text)
        remaining=20000
        for index,table in enumerate(tree.xpath('//table')[:10],1):
            rows=[]
            for row in table.xpath('.//tr')[:100]:
                cells=[' '.join(cell.text_content().split())[:1000] for cell in row.xpath('./th|./td')[:30]]
                value=' | '.join(cells)
                if len(value)>remaining: break
                remaining-=len(value)
                if value: rows.append(value)
            if rows: metadata['tables'].append({'table':index,'text':'\n'.join(rows)})
        metadata['tables_truncated']=remaining<2000 or metadata['html_table_count']>10
    except (ImportError,ValueError):
        metadata['table_warning']='Structured table parsing unavailable; inspect raw HTML.'
    metadata['date_warning'] = 'Extracted publication dates do not establish historical body availability.'
    return fallback, metadata, links
