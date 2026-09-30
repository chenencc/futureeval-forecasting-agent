"""Read-only research demo on one previously collected Metaculus question."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.monitor_tournament import API_ROOT, get_json

from tavily_research import followup_query_from_response, format_batch, search_batch


MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def ask_ultra(prompt: str, api_key: str, *, max_tokens: int) -> str:
    request = Request(
        OPENROUTER_URL,
        data=json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/chenencc/futureeval-forecasting-agent",
            "X-Title": "FutureEval read-only research demo",
        },
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        body = json.load(response)
    message = body["choices"][0]["message"]
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content.strip():
        raise ValueError("Ultra returned no visible answer")
    return content.strip()


def main() -> None:
    item = json.loads(Path("data/resolved_sample_metadata.json").read_text(encoding="utf-8"))["items"][1]
    question = item["title"]
    detail = None
    detail_error = None
    try:
        detail = get_json(f"{API_ROOT}{item['post_id']}/", os.environ["METACULUS_TOKEN"])
    except Exception as exc:
        detail_error = f"{type(exc).__name__}: {exc}"
    question_data = (detail or {}).get("question") or {}
    criteria = question_data.get("resolution_criteria") or (detail or {}).get("resolution_criteria") or "Unavailable; use the question wording only."
    fine_print = question_data.get("fine_print") or (detail or {}).get("fine_print") or ""

    tavily_key = os.environ["TAVILY_API_KEY"]
    ultra_key = os.environ["OPENROUTER_API_KEY"]
    first = search_batch(question, tavily_key)
    first_report = format_batch(first, label="Initial web search")
    gap_prompt = (
        "You are selecting a second Tavily web search for a forecasting question. "
        "Find the most consequential fact still missing after these results. "
        "Return exactly one line: QUERY: <specific search terms>. No explanation or forecast.\n\n"
        f"Question: {question}\nResolution criteria: {criteria}\nFine print: {fine_print}\n\n{first_report}"
    )
    gap_response = ask_ultra(gap_prompt, ultra_key, max_tokens=2048)
    followup_query = followup_query_from_response(gap_response, question)
    second = search_batch(
        followup_query,
        tavily_key,
        exclude_urls=tuple(hit["url"] for hit in first["results"]),
    )
    second_report = format_batch(second, label="Follow-up web search (new sources only)")
    final_prompt = (
        "Assess this Metaculus binary question using only the cited research below. "
        "This is a retrospective demonstration run as of now, not a historical backtest. "
        "Distinguish a documented resolution from your inference. "
        "Return PROBABILITY: N%, VERDICT: Yes/No/Uncertain, and a concise RATIONALE with source URLs. "
        "If evidence is insufficient, say so and avoid false certainty.\n\n"
        f"Question: {question}\nResolution criteria: {criteria}\nFine print: {fine_print}\n\n"
        f"{first_report}\n\n{second_report}"
    )
    final_response = ask_ultra(final_prompt, ultra_key, max_tokens=4096)
    probability_match = re.search(r"(?im)^\s*PROBABILITY\s*:\s*(\d{1,3}(?:\.\d+)?)\s*%", final_response)
    probability = float(probability_match.group(1)) / 100 if probability_match else None
    if probability is not None and not 0 <= probability <= 1:
        probability = None

    now = datetime.now(timezone.utc)
    output = Path("snapshots/demo") / now.strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at_utc": now.isoformat(),
        "mode": "read_only_retrospective_demo",
        "submitted_to_metaculus": False,
        "model": MODEL,
        "question": item,
        "resolution_criteria": criteria,
        "fine_print": fine_print,
        "question_detail_error": detail_error,
        "first_search": first,
        "gap_model_response": gap_response,
        "followup_query": followup_query,
        "second_search_new_sources": second,
        "model_final_response": final_response,
        "model_probability": probability,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"READ-ONLY DEMO: {question}")
    print(f"First search: {len(first['results'])} sources; second unique search: {len(second['results'])} sources")
    print(f"Follow-up query: {followup_query}")
    print(f"\n{first_report}\n\n{second_report}\n\nFINAL ASSESSMENT\n{final_response}")
    print(f"Saved report to {output / 'report.json'}")


if __name__ == "__main__":
    main()
