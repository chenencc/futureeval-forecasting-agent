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


def search_options(query, *, topic="general", include_domains=None, include_domains_mode="prefer", exact_match=False):
    """Validate model-selectable options without allowing depth/budget overrides."""
    query = " ".join(query.split())[:350]
    if not query or topic not in {"general", "news", "finance"}:
        raise ValueError("Valid query and general/news/finance topic required")
    if include_domains_mode not in {"prefer", "restrict"} or type(exact_match) is not bool:
        raise ValueError("Invalid domain mode or exact_match boolean")
    if exact_match and not re.search(r'"[^"\n]+"', query):
        raise ValueError('Exact matching requires an entity or phrase in double quotes')
    domains = [] if include_domains is None else include_domains
    if not isinstance(domains, list) or len(domains) > 10:
        raise ValueError("Choose at most ten official domains")
    normalized = []
    for domain in domains:
        if not isinstance(domain, str) or not re.fullmatch(r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", domain):
            raise ValueError("Use bare domains, without scheme/path/wildcard")
        domain = domain.lower()
        if domain == "metaculus.com" or domain.endswith(".metaculus.com"):
            raise ValueError("Metaculus remains excluded from research sources")
        if domain not in normalized:
            normalized.append(domain)
    if include_domains_mode == "restrict" and not normalized:
        raise ValueError("Restrict requires at least one domain")
    return {"topic": topic, "include_domains": normalized, "include_domains_mode": include_domains_mode, "exact_match": exact_match}


def search_batch(query: str, api_key: str, *, exclude_urls: tuple[str, ...] = (), end_date: str | None = None, start_date: str | None = None,
                 topic="general", include_domains=None, include_domains_mode="prefer", exact_match=False) -> dict:
    """Search once and retain at most ten new, distinct Tavily hits."""
    query = " ".join(query.split())[:350]
    if not query:
        raise ValueError("Search query is empty")
    options = search_options(query, topic=topic, include_domains=include_domains,
                             include_domains_mode=include_domains_mode, exact_match=exact_match)

    payload = {
        "query": query,
        "search_depth": "basic",
        "chunks_per_source": 3,
        "auto_parameters": False,
        "topic": options["topic"],
        "exact_match": options["exact_match"],
        "include_usage": True,
        "max_results": MAX_RESULTS,
        "include_answer": False,
        "include_raw_content": False,
        "include_published_date": True,
        "exclude_domains": ["metaculus.com"],
    }
    if options["include_domains"]:
        payload["include_domains"] = options["include_domains"]
        payload["include_domains_mode"] = options["include_domains_mode"]
    if end_date is not None:
        from datetime import date
        if date.fromisoformat(end_date).isoformat() != end_date:
            raise ValueError("end_date must be YYYY-MM-DD")
        payload["end_date"] = end_date
    if start_date is not None:
        from datetime import date
        date.fromisoformat(start_date)
        if end_date and start_date>end_date: raise ValueError('start_date must precede end_date')
        payload['start_date']=start_date
        payload['filter_by_published_date']=True
    request = Request(
        TAVILY_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        raw_response = json.load(response)
        raw_results = raw_response.get("results", [])

    seen_urls = {canonical_url(url) for url in exclude_urls}
    results = []
    for result in raw_results:
        url = result.get("url") or ""
        key = canonical_url(url)
        if not key or key in seen_urls:
            continue
        host = (urlsplit(url).hostname or "").lower()
        if host == "metaculus.com" or host.endswith(".metaculus.com"):
            continue
        if options["include_domains"] and options["include_domains_mode"] == "restrict" and not any(host == d or host.endswith("." + d) for d in options["include_domains"]):
            continue
        seen_urls.add(key)
        results.append({
            "title": result.get("title") or "Untitled source",
            "url": url,
            "published_date": result.get("published_date") or "date unknown",
            "content": (result.get("content") or "")[:2000],
            "snippet_truncated": len(result.get("content") or "") > 2000,
        })
        if len(results) == MAX_RESULTS:
            break

    return {
        "query": query,
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "raw_results": raw_results,
        "end_date": end_date,
        "search_options": options,
        "usage": raw_response.get("usage"),
        "request_id": raw_response.get("request_id"),
    }


def format_batch(batch: dict, *, label: str) -> str:
    lines = [
        f"{label} retrieved at {batch['searched_at']} for: {batch['query']}",
        "Search snippets are leads, not verified facts. Check dates and source credibility.",
    ]
    for result in batch["results"]:
        lines.append(
            f"- {result['title']} ({result['published_date']})\n"
            f"  {result['url']}\n  {' '.join(result['content'].split())}"
        )
    if not batch["results"]:
        lines.append("No new search results returned.")
    return "\n".join(lines)


def search_question(question_text: str, api_key: str) -> str:
    """Compatibility wrapper for a single ten-result search."""
    return format_batch(search_batch(question_text, api_key), label="Web search")
