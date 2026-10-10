"""Bounded local document access with exact character coordinates."""
from ForecastAgent.tavily_research import canonical_url
import hashlib
import json
import re


def version_digest(page):
    return hashlib.sha256(json.dumps({'content': page['content'], 'documents': page.get('documents', [])},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def documents(pages):
    for url, page in pages.items():
        docs = page.get("documents") or [{"page_content": page["content"], "metadata": {"format": "text"}}]
        for index, doc in enumerate(docs, 1):
            yield url, page, index, doc


class SavedSourceLookupError(ValueError):
    """A local lookup failed; original material may still be recoverable."""


class SavedSourceIdentityError(ValueError):
    """An alias refers to different immutable source versions."""


def select(pages, url, document_index=None):
    # Exact saved keys are opaque identities. Canonical URLs are aliases only.
    page = pages.get(url)
    if page is None:
        key = canonical_url(url)
        matches = [p for u, p in pages.items() if key and canonical_url(u) == key]
        if len({version_digest(p) for p in matches}) > 1:
            raise SavedSourceIdentityError('Ambiguous saved URL alias; source versions differ')
        page = matches[0] if matches else None
    if page is None:
        raise SavedSourceLookupError("Read only URLs already saved in this task")
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
    target = None
    if args.get("url"):
        page, _, _ = select(pages, args["url"])
        target = next(url for url, value in pages.items() if value is page)
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
    index = args.get('document_index')
    docs = page.get('documents') or [{'page_content':page['content']}]
    next_document = ({'url':args['url'],'document_index':index+1,'start_char':0}
                     if index is not None and end == len(text) and index < len(docs) else None)
    return {"url": args["url"], "content": text[start:end], "start_char": start, "end_char": end,
            "next_start": end if end < len(text) else None, "total_chars": len(text), "location": location,
            'next_document_args':next_document,
            'coordinate_instruction':'start_char belongs to this selected document only. At its end use next_document_args, not the same offset in another document. Omit document_index to navigate the whole saved body.',
            "source_sha256": page.get("sha256"), "temporal_status": page.get("temporal_status"),
            "source_truncated": bool(page.get("content_truncated") or page.get("documents_truncated"))}


def search_saved_text(pages, args):
    query = args.get("query", "")
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise ValueError("Use a nonempty literal query of at most 300 characters")
    limit = integer(args.get("limit", 10), 1, 20, "limit")
    offset = integer(args.get("offset", 0), 0, 1_000_000, "offset")
    target = None
    if args.get("url"):
        page, _, _ = select(pages, args["url"])
        target = next(url for url, value in pages.items() if value is page)
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
                                "excerpt_args": {"url": url, "document_index": index,
                                                 "start_char": start, "end_char": end},
                                "source_sha256": page.get("sha256"), "temporal_status": page.get("temporal_status")})
            total += 1; cursor = found + len(query)
    return {"query": query, "case_sensitive": True, "matches": matches, "total_matches": total,
            "next_offset": offset + len(matches) if offset + len(matches) < total else None,
            "scope": "Saved parsed text only; missing or truncated originals are not searched."}


def quote_coordinates(pages, args):
    """Locate exact text in a selected saved coordinate space; never guess offsets."""
    _, text, _ = select(pages, args['url'], args.get('document_index'))
    quote = args.get('quote')
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 4000:
        raise ValueError('Use an exact nonempty quote of at most 4000 characters from saved text')
    matches = []; cursor = 0
    while (found := text.find(quote, cursor)) >= 0:
        matches.append(found)
        cursor = found + 1
    if not matches:
        raise ValueError('Quote is absent from selected saved text; read_document or search_saved_text first')
    occurrence = args.get('occurrence_index')
    if occurrence is None:
        if len(matches) != 1:
            raise ValueError(f'Quote occurs {len(matches)} times; specify one-based occurrence_index or a longer unique quote')
        occurrence = 1
    integer(occurrence, 1, len(matches), 'occurrence_index')
    start = matches[occurrence - 1]
    return {**{k: args[k] for k in ('url', 'document_index', 'need_ids') if k in args},
            'start_char': start, 'end_char': start + len(quote)}


def find_passages(pages, args):
    """Bounded lexical navigation of saved text, with exact reusable offsets."""
    query = args.get('query')
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise ValueError('Use a nonempty passage query of at most 300 characters')
    terms = list(dict.fromkeys(re.findall(r'\w+', query.casefold())))[:20]
    limit = integer(args.get('limit', 5), 1, 10, 'limit')
    target = canonical_url(args['url']) if args.get('url') else None
    if args.get('url') and target not in pages:
        raise ValueError('Unknown saved URL')
    ranked = []
    for url, page, index, doc in documents(pages):
        if target and url != target:
            continue
        text = doc['page_content']
        for start in range(0, len(text), 1000):
            end = min(start + 1200, len(text))
            tokens = set(re.findall(r'\w+', text[start:end].casefold()))
            matched = [t for t in terms if t in tokens]
            if matched:
                # Include nearby heading/date and paragraph boundaries in the
                # exact source slice instead of handing out isolated headings.
                left = text.rfind('\n\n', max(0,start-500), start)
                left = left+2 if left >= 0 else start
                headings = list(re.finditer(r'(?m)^#{1,6}\s+[^\n]+',text[max(0,start-1200):start]))
                if headings:
                    left = min(left,max(0,start-1200)+headings[-1].start())
                right = text.find('\n\n',end,min(len(text),end+800))
                right = right if right >= 0 else end
                right = min(right,left+4000)
                ranked.append((len(matched), {'url': url, 'document_index': index, 'content': text[left:right],
                    'identity_preview':page['content'][:450],
                    'matched_terms': matched, 'excerpt_args': {'url': url, 'document_index': index, 'start_char': left, 'end_char': right},
                    'source_sha256': page.get('sha256'), 'metadata': doc.get('metadata', {})}))
    ranked.sort(key=lambda row: -row[0])
    return {'query': query, 'passages': [row for _, row in ranked[:limit]], 'matching_windows': len(ranked),
            'scope': 'Lexical navigation of saved text; not a relevance verdict, exhaustive retrieval or factual verification.'}
