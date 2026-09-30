"""Bounded, read-only Ultra research with auditable public-source snapshots."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from tavily_research import canonical_url, search_batch


MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_SEARCHES = 3
MAX_FETCHES = 5
MAX_FINDS = 10
MAX_TURNS = 18
MAX_PAGE_BYTES = 1_500_000
MAX_SAVED_CHARS = 150_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_url(url: str) -> bool:
    """Reject local/credentialed targets, including redirects to them."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return False
    hostname = parts.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    return bool(addresses) and all(
        ipaddress.ip_address(item[4][0]).is_global for item in addresses
    )


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_url(newurl):
            raise ValueError("Redirect target is not a public HTTP URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ReadableHTML(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
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


def fetch_public_page(url: str) -> dict:
    if not public_url(url):
        raise ValueError("URL is not a public HTTP URL")
    request = Request(url, headers={"User-Agent": "FutureEvalReadOnlyResearch/0.2", "Accept": "text/html,text/plain"})
    with build_opener(SafeRedirects()).open(request, timeout=20) as response:
        final_url = response.geturl()
        if not public_url(final_url):
            raise ValueError("Final URL is not public")
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
            raise ValueError(f"Unsupported content type: {content_type}")
        raw = response.read(MAX_PAGE_BYTES + 1)
        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError("Page exceeds size limit")
        charset = response.headers.get_content_charset() or "utf-8"
    decoded = raw.decode(charset, errors="replace")
    if content_type in {"text/html", "application/xhtml+xml"}:
        parser = ReadableHTML()
        parser.feed(decoded)
        decoded = "".join(parser.parts)
    content = re.sub(r"[ \t]+", " ", decoded)
    content = re.sub(r"\n\s*\n+", "\n", content).strip()[:MAX_SAVED_CHARS]
    if not content:
        raise ValueError("Page contained no readable text")
    return {
        "url": url,
        "final_url": final_url,
        "retrieved_at_utc": utc_now(),
        "content_type": content_type,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content": content,
        "content_truncated": len(decoded) > MAX_SAVED_CHARS,
    }


def ask_ultra(messages: list[dict], api_key: str, *, first_turn: bool = False) -> dict:
    request = Request(
        OPENROUTER_URL,
        data=json.dumps({
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": {"type": "function", "function": {"name": "search_tavily"}} if first_turn else "auto",
            "temperature": 0.2,
            "max_tokens": 3000,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/chenencc/futureeval-forecasting-agent",
            "X-Title": "FutureEval read-only Ultra research",
        },
        method="POST",
    )
    for attempt in range(2):
        try:
            with urlopen(request, timeout=180) as response:
                payload = json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            if attempt == 0 and exc.code in {429, 500, 502, 503, 504}:
                time.sleep(3)
                continue
            raise RuntimeError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict) and isinstance(choices[0].get("message"), dict):
            return choices[0]["message"]
        error = payload.get("error") if isinstance(payload, dict) else None
        if attempt == 0:
            time.sleep(3)
            continue
        raise RuntimeError(f"OpenRouter returned no choices: {str(error or payload)[:800]}")
    raise AssertionError("Unreachable OpenRouter retry state")


TOOLS = [
    {"type": "function", "function": {
        "name": "search_tavily",
        "description": "Search public web results with Tavily basic. At most three calls total, each returning up to ten new URLs.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "fetch_page",
        "description": "Read one public page previously found by search. No paid extraction; long pages can be inspected with find_in_page.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "find_in_page",
        "description": "Find text within a previously fetched full page; useful when the initial excerpt was truncated.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "term": {"type": "string"}}, "required": ["url", "term"]},
    }},
    {"type": "function", "function": {
        "name": "finish_research",
        "description": "Finish with a read-only assessment. Evidence quality is a rubric, not the event probability.",
        "parameters": {"type": "object", "properties": {
            "probability": {"type": "number", "description": "Probability of Yes, from 0 to 1."},
            "verdict": {"type": "string", "enum": ["Yes", "No", "Uncertain"]},
            "base_rate": {"type": "string"},
            "event_paths": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "object", "properties": {
                "url": {"type": "string"}, "claim": {"type": "string"},
                "source_type": {"type": "string", "enum": ["primary", "secondary", "unknown"]},
                "quality": {"type": "string", "enum": ["high", "medium", "low", "unverified"]},
                "evidence_chain": {"type": "string", "description": "Original dataset or reporting chain behind this claim; reports citing the same original data use the same label."},
                "reason": {"type": "string"},
            }, "required": ["url", "claim", "source_type", "quality", "evidence_chain", "reason"]}},
            "contradictions": {"type": "array", "items": {"type": "string"}},
            "remaining_unknowns": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        }, "required": ["probability", "verdict", "base_rate", "event_paths", "evidence", "contradictions", "remaining_unknowns", "rationale"]},
    }},
]


SYSTEM_PROMPT = """You are an evidence-led forecasting research agent. This is READ ONLY: never submit a prediction or trade.
You may choose Tavily basic searches (at most 3), public page fetches (at most 5), and find_in_page.
Start with a targeted search. Open the most consequential sources before deciding whether another search is needed.
Search snippets are leads, not proof. Assess each important claim against the exact resolution criteria,
publication date, original data, and source independence. Multiple articles citing the same data are one evidence chain.
Evidence quality rubric: high = a fetched primary record directly covering the criterion and time window;
medium = a traceable secondary report or a primary record with an unresolved interpretation;
low = indirect, stale, or weakly documented support; unverified = a claim the fetched text does not substantiate.
Explain the rating for each cited claim. Do not turn these ordinal labels into numerical probabilities.
Name the original evidence_chain for each claim. Reports repeating the same original dataset share one label.
If the first search yields only secondary reports, use another search targeted at the original data before finishing.
If original pages refuse access, state that limit and use at most medium quality for traceable secondary reports.
When search and fetch budgets are exhausted, finish with the evidence available and explicit uncertainty.
Do not claim to have verified a page you did not fetch. A high quality rating does not mechanically determine event probability.
If evidence is poor, state uncertainty. Call finish_research with a concise structured final result.
"""


def validate_assessment(data: dict, pages: dict[str, dict]) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Assessment must be an object")
    probability = data.get("probability")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
        raise ValueError("probability must be a number between 0 and 1")
    if data.get("verdict") not in {"Yes", "No", "Uncertain"}:
        raise ValueError("Invalid verdict")
    evidence = data.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    for item in evidence:
        if not isinstance(item, dict) or canonical_url(item.get("url", "")) not in pages:
            raise ValueError("Evidence must cite a fetched page")
        if item.get("source_type") not in {"primary", "secondary", "unknown"}:
            raise ValueError("Invalid source_type")
        if item.get("quality") not in {"high", "medium", "low", "unverified"}:
            raise ValueError("Invalid evidence quality")
        if item["quality"] == "high" and item["source_type"] != "primary":
            raise ValueError("High quality requires a fetched primary record")
        if not isinstance(item.get("evidence_chain"), str) or not item["evidence_chain"].strip():
            raise ValueError("Evidence needs its original evidence_chain")
        if not item.get("claim") or not item.get("reason"):
            raise ValueError("Evidence needs a claim and reason")
    for key in ("event_paths", "contradictions", "remaining_unknowns"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"{key} must be a list")
    if not isinstance(data.get("base_rate"), str) or not isinstance(data.get("rationale"), str):
        raise ValueError("base_rate and rationale must be strings")
    return data


def run_research(question: str, criteria: str, fine_print: str, tavily_key: str, router_key: str) -> dict:
    started = utc_now()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Research as of {started}.\nQuestion: {question}\nResolution criteria: {criteria}\nFine print: {fine_print}"},
    ]
    searches: list[dict] = []
    pages: dict[str, dict] = {}
    discovered: set[str] = set()
    finds = 0
    transcript: list[dict] = []
    assessment = None
    error = None

    empty_responses = 0
    for turn in range(MAX_TURNS):
        try:
            message = ask_ultra(messages, router_key, first_turn=(turn == 0))
        except Exception as exc:
            error = f"Ultra request failed: {type(exc).__name__}: {exc}"
            break
        calls = message.get("tool_calls") or []
        if not calls:
            transcript.append({"message": message.get("content")})
            empty_responses += 1
            if empty_responses >= 2:
                error = "Ultra ended without finish_research tool call"
                break
            messages.append({"role": "assistant", "content": message.get("content") or ""})
            messages.append({"role": "user", "content": "Please call finish_research with the structured result. Use only fetched pages as evidence; if none were fetched, report uncertainty."})
            continue
        empty_responses = 0
        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
        for call in calls:
            name = (call.get("function") or {}).get("name")
            args = {}
            try:
                args = json.loads((call.get("function") or {}).get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError("Tool arguments must be an object")
                if name == "search_tavily":
                    if len(searches) >= MAX_SEARCHES:
                        raise ValueError("Three-search budget exhausted")
                    query = args.get("query", "")
                    # Count attempts, including failures and retries, against the task budget.
                    searches.append({"query": query, "attempted_at_utc": utc_now(), "results": []})
                    batch = search_batch(query, tavily_key, exclude_urls=tuple(discovered))
                    searches[-1] = batch
                    discovered.update(canonical_url(hit["url"]) for hit in batch["results"])
                    result = batch
                elif name == "fetch_page":
                    url = args.get("url", "")
                    key = canonical_url(url)
                    if key not in discovered:
                        raise ValueError("URL was not found by this task's searches")
                    if key not in pages and len(pages) >= MAX_FETCHES:
                        raise ValueError("Five-page fetch budget exhausted")
                    if key not in pages:
                        pages[key] = fetch_public_page(url)
                    page = pages[key]
                    result = {k: v for k, v in page.items() if k != "content"}
                    result["content_excerpt"] = page["content"][:16_000]
                    result["excerpt_truncated"] = len(page["content"]) > 16_000
                elif name == "find_in_page":
                    if finds >= MAX_FINDS:
                        raise ValueError("Page-find budget exhausted")
                    finds += 1
                    page = pages.get(canonical_url(args.get("url", "")))
                    term = args.get("term", "").strip()[:100]
                    if not page or len(term) < 2:
                        raise ValueError("Use a fetched URL and a term of at least two characters")
                    content = page["content"]
                    matches = list(re.finditer(re.escape(term), content, re.IGNORECASE))[:8]
                    result = {"url": page["url"], "term": term, "matches": [
                        content[max(0, match.start() - 350):min(len(content), match.end() + 350)]
                        for match in matches
                    ]}
                elif name == "finish_research":
                    if not any(item.get("source_type") == "primary" for item in args.get("evidence", []) or []) and len(searches) < 2:
                        raise ValueError("Search for an original/primary data source before finishing without one")
                    assessment = validate_assessment(args, pages)
                    result = {"accepted": True, "submitted_to_metaculus": False}
                else:
                    raise ValueError("Unknown tool")
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            transcript.append({"tool": name, "arguments": args if isinstance(args, dict) else {}, "result": result})
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)})
        if assessment is not None:
            break
    else:
        error = "Ultra exceeded the research turn limit"

    return {
        "started_at_utc": started,
        "completed_at_utc": utc_now(),
        "mode": "read_only_ultra_agent",
        "submitted_to_metaculus": False,
        "model": MODEL,
        "question": question,
        "resolution_criteria": criteria,
        "fine_print": fine_print,
        "search_budget": MAX_SEARCHES,
        "searches_used": len(searches),
        "searches": searches,
        "pages": list(pages.values()),
        "tool_transcript": transcript,
        "assessment": assessment,
        "evidence_chains": sorted({item["evidence_chain"].strip().casefold() for item in assessment["evidence"]}) if assessment else [],
        "error": error,
    }
