"""Bounded downloading only; decoding and parsing belong to readers."""
from urllib.request import Request

SUPPORTED = {"text/html", "text/plain", "application/xhtml+xml", "application/pdf", "text/csv", "application/csv", "application/json",
             'application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml'}


def download(url, *, public_check, opener_factory, max_page_bytes=1_500_000, user_agent=None):
    if not public_check(url):
        raise ValueError("URL is not a public HTTP URL")
    request = Request(url, headers={"User-Agent": user_agent or "FutureEvalReadOnlyResearch/0.3", "Accept": ",".join(sorted(SUPPORTED))})
    with opener_factory().open(request, timeout=20) as response:
        final_url = response.geturl()
        if not public_check(final_url):
            raise ValueError("Final URL is not public")
        content_type = response.headers.get_content_type()
        if content_type not in SUPPORTED and content_type != 'application/octet-stream':
            raise ValueError(f"Unsupported content type: {content_type}")
        byte_limit = 8_000_000 if content_type == "application/pdf" else max_page_bytes
        raw = response.read(byte_limit + 1)
        if len(raw) > byte_limit:
            raise ValueError("Page exceeds size limit")
        if content_type == 'application/octet-stream' and not raw.lstrip().startswith(b'%PDF-'):
            raise ValueError('Unsupported binary body; only PDF magic is recognized')
        charset = response.headers.get_content_charset() or "utf-8"
    return {"url": url, "final_url": final_url, "content_type": content_type, "charset": charset, "raw": raw}
