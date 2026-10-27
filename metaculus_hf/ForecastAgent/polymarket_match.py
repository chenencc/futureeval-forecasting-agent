"""Conservative Polymarket Gamma search for point-in-time market candidates.

Inspired by No-Stream/nostreambot-metaculus-bot (MIT). Candidates are never
treated as verified equivalent contracts or executable paper-trade quotes.
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
_STOP = {"a", "an", "the", "will", "by", "before", "of", "in", "on", "at", "to", "be", "is", "and"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if result == result and result not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _array(value: Any) -> list:
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _yes_index(market: dict[str, Any]) -> int | None:
    outcomes = [str(value).strip().lower() for value in _array(market.get("outcomes"))]
    return outcomes.index("yes") if len(outcomes) == 2 and set(outcomes) == {"yes", "no"} else None


def _yes_price(market: dict[str, Any]) -> float | None:
    index = _yes_index(market)
    prices = _array(market.get("outcomePrices"))
    if index is None or len(prices) != 2:
        return None
    parsed = [_number(value) for value in prices]
    if any(value is None or not 0 <= value <= 1 for value in parsed):
        return None
    if abs(sum(parsed) - 1) > 0.02:
        return None
    price = parsed[index]
    volume = _number(market.get("volumeNum") or market.get("volume")) or 0
    interest = _number(market.get("openInterest")) or 0
    if price == 0.5 and volume <= 0 and interest <= 0:
        return None
    return price


def _yes_token_id(market: dict[str, Any]) -> str | None:
    tokens = _array(market.get("clobTokenIds"))
    index = _yes_index(market)
    if index is not None and len(tokens) == 2 and all(isinstance(token, str) and token for token in tokens) and tokens[0] != tokens[1]:
        return tokens[index]
    return None


def _quotes(market: dict[str, Any]) -> tuple[float | None, float | None]:
    # Gamma quotes are not a verified YES token order book; avoid assigning
    # the first outcome's quotes to YES for reversed/nonbinary contracts.
    if _yes_index(market) != 0:
        return None, None
    bid, ask = _number(market.get("bestBid")), _number(market.get("bestAsk"))
    bid = bid if bid is not None and 0 <= bid <= 1 else None
    ask = ask if ask is not None and 0 <= ask <= 1 else None
    return (None, None) if bid is not None and ask is not None and bid > ask else (bid, ask)


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
        if score < 0.3:
            continue
        price = _yes_price(market)
        bid, ask = _quotes(market)
        slug = str(market.get("slug") or "")
        candidates.append({
            "market_id": market_id,
            "condition_id": market.get("conditionId"),
            "yes_token_id": _yes_token_id(market),
            "event_title": event_title,
            "market_title": title,
            "market_slug": slug,
            "market_url": f"https://polymarket.com/event/{event_slug}" if event_slug else (
                f"https://polymarket.com/market/{slug}" if slug else None
            ),
            "resolution_rules": market.get("description") or event_rules,
            "market_end_time": market.get("endDate"),
            "yes_probability_display": price,
            "best_bid": bid,
            "best_ask": ask,
            "volume_total": _number(market.get("volumeNum") or market.get("volume")),
            "liquidity": _number(market.get("liquidityNum") or market.get("liquidity")),
            "match_score": score,
            "match_status": "year_conflict" if conflict else "candidate_needs_resolution_review",
            "eligible_for_edge": False,
            "required_review": ["entity", "event_stage", "time_window_and_timezone", "threshold_and_quantifier", "geographic_scope", "resolution_source"],
            "price_status": "unquoted_or_untraded" if price is None else "display_price_only",
        })
    candidates.sort(key=lambda row: (row["match_status"] == "year_conflict", -row["match_score"]))
    return candidates[:limit]


def search_candidates(question_text: str, *, query: str | None = None, as_of_utc: str | None = None, page: int = 1) -> dict[str, Any]:
    if type(page) is not int or not 1 <= page <= 3:
        raise ValueError("Market page must be between one and three")
    searched_at = datetime.now(UTC).isoformat()
    if as_of_utc is not None:
        cutoff = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise ValueError("as_of_utc must include timezone")
        if cutoff < datetime.fromisoformat(searched_at):
            return {"searched_at": searched_at, "candidates": [], "error": "historical_snapshot_required",
                    "as_of_utc": as_of_utc, "historical_safe": False}
    query = " ".join((question_text if query is None else query).split())[:250]
    if not query:
        return {"searched_at": searched_at, "query": query, "candidates": [], "error": "empty_question"}
    url = SEARCH_URL + "?" + urlencode({"q": query, "limit_per_type": 10, "events_status": "active", "page": page})
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "futureeval-research-bot/0.1"})
    with urlopen(request, timeout=15) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Polymarket response exceeds 8 MB")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Polymarket search response was not an object")
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {"searched_at": searched_at, "query": query, "endpoint": url,
            "snapshot_type": "current_market_discovery", "historical_safe": False,
            "pagination": payload.get("pagination"), "raw_response": payload,
            "raw_response_sha256": hashlib.sha256(canonical).hexdigest(),
            "candidates": parse_candidates(payload, question_text)}
