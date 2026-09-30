"""Bounded local document access with exact character coordinates."""
from ForecastAgent.tavily_research import canonical_url
import hashlib
import json


def version_digest(page):
    return hashlib.sha256(json.dumps({'content': page['content'], 'documents': page.get('documents', [])},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def documents(pages):
    for url, page in pages.items():
        docs = page.get("documents") or [{"page_content": page["content"], "metadata": {"format": "text"}}]
        for index, doc in enumerate(docs, 1):
            yield url, page, index, doc


def select(pages, url, document_index=None):
    page = pages.get(canonical_url(url))
    if page is None:
        raise ValueError("Read only URLs already saved in this task")
    if document_index is None:
        return page, page["content"], {"coordinate_space": "saved_content"}
    docs = page.get("documents") or [{"page_content": page["content"], "metadata": {"format": "text"}}]
    if type(document_index) is not int or not 1 <= document_index <= len(docs):
        raise ValueError("Invalid one-based document index")
    doc = docs[document_index - 1]
    return page, doc["page_content"], {**doc.get("metadata", {}), "coordinate_space": "saved_document", "document_index": document_index}


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Invalid " + label)
    return value


def list_documents(pages, args):
    offset = integer(args.get("offset", 0), 0, 1_000_000, "offset")
    limit = integer(args.get("limit", 20), 1, 100, "limit")
    target = canonical_url(args["url"]) if args.get("url") else None
    if args.get("url") and target not in pages:
        raise ValueError("Unknown saved URL")
    rows = [{"url": url, "document_index": index, "chars": len(doc["page_content"]),
             "metadata": doc.get("metadata", {}), "source_sha256": page.get("sha256"),
             "temporal_status": page.get("temporal_status"),
             "source_truncated": bool(page.get("documents_truncated") or page.get("content_truncated"))}
            for url, page, index, doc in documents(pages) if target is None or url == target]
    end = min(offset + limit, len(rows))
    return {"documents": rows[offset:end], "total": len(rows), "next_offset": end if end < len(rows) else None}


def read_document(pages, args):
    page, text, location = select(pages, args["url"], args.get("document_index"))
    start = integer(args.get("start_char", 0), 0, len(text), "start_char")
    count = integer(args.get("max_chars", 6000), 1, 18000, "max_chars")
    end = min(start + count, len(text))
    return {"url": args["url"], "content": text[start:end], "start_char": start, "end_char": end,
            "next_start": end if end < len(text) else None, "total_chars": len(text), "location": location,
            "source_sha256": page.get("sha256"), "temporal_status": page.get("temporal_status"),
            "source_truncated": bool(page.get("content_truncated") or page.get("documents_truncated"))}


def search_saved_text(pages, args):
    query = args.get("query", "")
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise ValueError("Use a nonempty literal query of at most 300 characters")
    limit = integer(args.get("limit", 10), 1, 20, "limit")
    offset = integer(args.get("offset", 0), 0, 1_000_000, "offset")
    target = canonical_url(args["url"]) if args.get("url") else None
    if args.get("url") and target not in pages:
        raise ValueError("Unknown saved URL")
    matches = []; total = 0
    # Case-sensitive literal matching preserves exact Unicode character offsets.
    for url, page, index, doc in documents(pages):
        if target is not None and url != target:
            continue
        text = doc["page_content"]; cursor = 0
        while (found := text.find(query, cursor)) >= 0:
            if offset <= total < offset + limit:
                start, end = max(0, found - 250), min(len(text), found + len(query) + 250)
                matches.append({"url": url, "document_index": index, "match_start": found,
                                "match_end": found + len(query), "context_start": start, "context_end": end,
                                "context": text[start:end], "metadata": doc.get("metadata", {}),
                                "source_sha256": page.get("sha256"), "temporal_status": page.get("temporal_status")})
            total += 1; cursor = found + len(query)
    return {"query": query, "case_sensitive": True, "matches": matches, "total_matches": total,
            "next_offset": offset + len(matches) if offset + len(matches) < total else None,
            "scope": "Saved parsed text only; missing or truncated originals are not searched."}
