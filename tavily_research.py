"""Small, one-credit Tavily search for a forecasting question."""

import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen


TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def search_question(question_text: str, api_key: str) -> str:
    """Return source-labelled search snippets, or fail on an API error."""
    query = " ".join(question_text.split())[:350]
    if not query:
        raise ValueError("Question text is empty; cannot search Tavily")

    payload = {
            "query": query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
            "include_published_date": True,
            "exclude_domains": ["metaculus.com"],
    }
    request = Request(
        TAVILY_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        results = json.load(response).get("results", [])
    searched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"Web search retrieved at {searched_at} for: {query}",
        "Search snippets are leads, not verified facts. Check dates and source credibility.",
    ]
    seen_urls: set[str] = set()
    for result in results:
        url = result.get("url", "")
        if not url.startswith(("https://", "http://")) or url in seen_urls:
            continue
        seen_urls.add(url)
        title = result.get("title") or "Untitled source"
        published = result.get("published_date") or "date unknown"
        snippet = " ".join((result.get("content") or "").split())[:1200]
        lines.append(f"- {title} ({published})\n  {url}\n  {snippet}")

    if not seen_urls:
        lines.append("No relevant search results returned; rely on question background.")
    return "\n".join(lines)
