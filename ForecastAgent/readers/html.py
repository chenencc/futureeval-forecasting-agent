from html.parser import HTMLParser
from urllib.parse import urljoin
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
    return "".join(parser.parts), metadata, links
