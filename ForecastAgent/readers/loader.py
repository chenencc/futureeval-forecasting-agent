"""One normalized snapshot contract for existing free document readers."""
import base64
import hashlib
import re
from ForecastAgent.evidence.document import Document
from ForecastAgent.readers.html import parse_html
from ForecastAgent.readers.pdf import parse_pdf
from ForecastAgent.readers.structured import parse_csv, parse_json


def load_response(response, *, retrieved_at, max_chars=150_000):
    raw = response["raw"]; source = response["final_url"]; kind = response["content_type"]
    metadata = {}; links = []
    if kind == "application/pdf":
        documents = parse_pdf(raw, source)
        text = "\n".join(f"[PDF page {d.metadata['page']}]\n{d.page_content}" for d in documents)
    else:
        text = raw.decode(response.get("charset", "utf-8"), errors="replace")
        if kind in {"text/html", "application/xhtml+xml"}:
            text, metadata, links = parse_html(text, source)
            documents = [Document(text, {"source": source, "format": "html"})]
        elif kind in {"text/csv", "application/csv"}:
            documents = parse_csv(text, source)
        elif kind == "application/json":
            documents = parse_json(text, source)
        else:
            documents = [Document(text, {"source": source, "format": "text"})]
    content = re.sub(r"[ \t]+", " ", text)
    content = re.sub(r"\n\s*\n+", "\n", content).strip()[:max_chars]
    if not content:
        raise ValueError("Page contained no readable text")
    # Original bytes always remain complete within the download byte limit.
    # Parsed documents have a shared text/count cap, not one cap per row/page.
    saved = []; remaining = max_chars
    for document in documents[:1000]:
        if remaining <= 0:
            break
        text_part = document.page_content[:remaining]; remaining -= len(text_part)
        saved.append(Document(text_part, {**document.metadata, "truncated": len(text_part) < len(document.page_content)}).as_dict())
    return {"url": response["url"], "final_url": source, "retrieved_at_utc": retrieved_at,
            "content_type": kind, "sha256": hashlib.sha256(raw).hexdigest(),
            "raw_response_base64": base64.b64encode(raw).decode("ascii"),
            "page_date_metadata": metadata, "content": content,
            "content_truncated": len(text) > max_chars, "links": links,
            "capture_method": "pdf_text" if kind == "application/pdf" else "direct_http",
            "documents": saved, "document_count": len(documents),
            "documents_truncated": len(saved) < len(documents) or any(d["metadata"]["truncated"] for d in saved)}
