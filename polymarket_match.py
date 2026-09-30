"""Conservative Polymarket Gamma search for point-in-time market candidates.

Inspired by No-Stream/nostreambot-metaculus-bot (MIT). Candidates are never
treated as verified equivalent contracts or executable paper-trade quotes.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
_STOP = {"a", "an", "the", "will", "by", "before", "of", "in", "on", "at", "to", "be", "is", "and"}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result and result not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _yes_price(market: dict[str, Any]) -> float | None:
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")
    try:
        outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
        prices = json.loads(prices) if isinstance(prices, str) else prices
    except (ValueError, TypeError):
        return None
    if not isinstance(outcomes, list) or not isinstance(prices, list):
        return None
    if len(outcomes) != 2 or len(prices) != 2 or str(outcomes[0]).lower() != "yes":
        return None
    price = _number(prices[0])
    if price is None or not 0 <= price <= 1:
        return None
    volume = _number(market.get("volumeNum") or market.get("volume")) or 0
    interest = _number(market.get("openInterest")) or 0
    if price == 0.5 and volume <= 0 and interest <= 0:
        return None
    return price


def _yes_token_id(market: dict[str, Any]) -> str | None:
    tokens = market.get("clobTokenIds")
    outcomes = market.get("outcomes")
    try:
        tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
        outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    except (ValueError, TypeError):
        return None
    if isinstance(tokens, list) and tokens and isinstance(outcomes, list) and outcomes and str(outcomes[0]).lower() == "yes":
        return str(tokens[0])
    return None


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in _STOP}


def _similarity(question: str, market: str) -> float:
    first, second = _tokens(question), _tokens(market)
    if not first or not second:
        return 0.0
    overlap = len(first & second) / len(first | second)
    ordered = SequenceMatcher(None, " ".join(sorted(first)), " ".join(sorted(second))).ratio()
    return round(0.65 * overlap + 0.35 * ordered, 3)


def _year_conflict(question: str, market: str) -> bool:
    q_years = set(re.findall(r"\b20\d{2}\b", question))
    m_years = set(re.findall(r"\b20\d{2}\b", market))
    return bool(q_years and m_years and q_years != m_years)


def parse_candidates(payload: Any, question_text: str, *, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Polymarket search response was not an object")
    candidates: list[dict[str, Any]] = []
    events = payload.get("events") or []
    market_rows: list[tuple[dict[str, Any], str, str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_title = str(event.get("title") or "")
        event_slug = str(event.get("slug") or "")
        event_rules = str(event.get("description") or "")
        for market in event.get("markets") or []:
            if isinstance(market, dict):
                market_rows.append((market, event_title, event_slug, event_rules))
    if not market_rows:
        market_rows.extend((market, "", "", "") for market in payload.get("markets") or [] if isinstance(market, dict))

    seen: set[str] = set()
    for market, event_title, event_slug, event_rules in market_rows:
        if market.get("closed") or market.get("resolved") or market.get("active") is False:
            continue
        market_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
        if not market_id or market_id in seen:
            continue
        seen.add(market_id)
        title = str(market.get("question") or market.get("title") or "")
        if not title:
            continue
        conflict = _year_conflict(question_text, title)
        score = _similarity(question_text, title)
        if score < 0.3 and not conflict:
            continue
        price = _yes_price(market)
        slug = str(market.get("slug") or "")
        candidates.append({
            "market_id": market_id,
            "condition_id": market.get("conditionId"),
            "yes_token_id": _yes_token_id(market),
            "event_title": event_title,
            "market_title": title,
            "market_url": f"https://polymarket.com/event/{event_slug}" if event_slug else (
                f"https://polymarket.com/market/{slug}" if slug else None
            ),
            "resolution_rules": market.get("description") or event_rules,
            "market_end_time": market.get("endDate"),
            "yes_probability_display": price,
            "best_bid": _number(market.get("bestBid")),
            "best_ask": _number(market.get("bestAsk")),
            "volume_total": _number(market.get("volumeNum") or market.get("volume")),
            "liquidity": _number(market.get("liquidityNum") or market.get("liquidity")),
            "match_score": score,
            "match_status": "year_conflict" if conflict else "candidate_needs_resolution_review",
            "price_status": "unquoted_or_untraded" if price is None else "display_price_only",
        })
    candidates.sort(key=lambda row: (row["match_status"] == "year_conflict", -row["match_score"]))
    return candidates[:limit]


def search_candidates(question_text: str) -> dict[str, Any]:
    searched_at = datetime.now(UTC).isoformat()
    query = " ".join(question_text.split())[:250]
    if not query:
        return {"searched_at": searched_at, "query": query, "candidates": [], "error": "empty_question"}
    url = SEARCH_URL + "?" + urlencode({"q": query, "limit_per_type": 10, "events_status": "active"})
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "futureeval-research-bot/0.1"})
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    return {"searched_at": searched_at, "query": query, "candidates": parse_candidates(payload, question_text)}
