"""Tavily search batches for a forecasting question."""

import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_RESULTS = 10


def followup_query_from_response(response: str, question_text: str) -> str:
    """Extract one Tavily query, with a fallback for malformed model output."""
    match = re.search(r"(?im)^\s*(?:QUERY|搜索词|查询词)\s*[:：]\s*(.+)$", response)
    if match:
        query = " ".join(match.group(1).strip(" `\"'").split())[:350]
        if query and query.casefold() != " ".join(question_text.split())[:350].casefold():
            return query
    return " ".join(question_text.split())[:300] + " latest official status resolution evidence"


def canonical_url(url: str) -> str:
    """Ignore fragments and common tracking parameters when comparing sources."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    query = urlencode([
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", query, ""))


def search_batch(query: str, api_key: str, *, exclude_urls: tuple[str, ...] = (), end_date: str | None = None) -> dict:
    """Search once and retain at most ten new, distinct Tavily hits."""
    query = " ".join(query.split())[:350]
    if not query:
        raise ValueError("Search query is empty")

    payload = {
        "query": query,
        "search_depth": "basic",
        "topic": "general",
        "max_results": MAX_RESULTS,
        "include_answer": False,
        "include_raw_content": False,
        "include_published_date": True,
        "exclude_domains": ["metaculus.com"],
    }
    if end_date is not None:
        from datetime import date
        if date.fromisoformat(end_date).isoformat() != end_date:
            raise ValueError("end_date must be YYYY-MM-DD")
        payload["end_date"] = end_date
    request = Request(
        TAVILY_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        raw_results = json.load(response).get("results", [])

    seen_urls = {canonical_url(url) for url in exclude_urls}
    results = []
    for result in raw_results:
        url = result.get("url") or ""
        key = canonical_url(url)
        if not key or key in seen_urls:
            continue
        seen_urls.add(key)
        results.append({
            "title": result.get("title") or "Untitled source",
            "url": url,
            "published_date": result.get("published_date") or "date unknown",
            "content": " ".join((result.get("content") or "").split())[:1200],
        })
        if len(results) == MAX_RESULTS:
            break

    return {
        "query": query,
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "raw_results": raw_results,
        "end_date": end_date,
    }


def format_batch(batch: dict, *, label: str) -> str:
    lines = [
        f"{label} retrieved at {batch['searched_at']} for: {batch['query']}",
        "Search snippets are leads, not verified facts. Check dates and source credibility.",
    ]
    for result in batch["results"]:
        lines.append(
            f"- {result['title']} ({result['published_date']})\n"
            f"  {result['url']}\n  {result['content']}"
        )
    if not batch["results"]:
        lines.append("No new search results returned.")
    return "\n".join(lines)


def search_question(question_text: str, api_key: str) -> str:
    """Compatibility wrapper for a single ten-result search."""
    return format_batch(search_batch(question_text, api_key), label="Web search")
