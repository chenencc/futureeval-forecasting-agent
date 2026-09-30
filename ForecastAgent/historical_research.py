"""Download resolved questions and run retrospective, read-only pipeline trials."""

import json
import os
from pathlib import Path
from urllib.parse import urlencode

from ForecastAgent.monitor_tournament import API_ROOT, get_json
from ForecastAgent.ultra_research_agent import run_research, utc_now


def run_sample(token: str, tavily_key: str, router_key: str, output: Path, *, tournament: str = "spring-aib-2026", research_limit: int = 3) -> dict:
    if not 1 <= research_limit <= 3:
        raise ValueError("Historical trial supports one to three questions")
    output.mkdir(parents=True, exist_ok=True)
    params = urlencode({"tournaments": tournament, "statuses": "resolved", "forecast_type": "binary", "limit": 5, "include_description": "true"})
    data = get_json(API_ROOT + "?" + params, token)
    posts = data["results"][:5]
    downloaded_at = utc_now()
    (output / "questions.json").write_text(json.dumps({"downloaded_at_utc": downloaded_at, "tournament": tournament, "posts": posts}, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for post in posts[:research_limit]:
        q = post.get("question") or {}
        row = {"post_id": post.get("id"), "question_id": q.get("id"), "title": q.get("title") or post.get("title"), "url": f"https://www.metaculus.com/questions/{post.get('id')}/"}
        criteria = q.get("resolution_criteria") or post.get("resolution_criteria")
        try:
            if q.get("type") != "binary" or (q.get("status") or post.get("status")) != "resolved":
                raise ValueError("Expected a resolved binary question")
            if not criteria:
                raise ValueError("Downloaded question lacks resolution criteria; refusing title-only forecast")
            print(f"RESEARCH {row['post_id']}: {row['title']}", flush=True)
            # Resolution labels and community forecasts never enter the prompt.
            # Current web research can still reveal outcomes, so this is not OOS.
            report = run_research(row["title"], criteria, q.get("fine_print") or "", tavily_key, router_key)
            report.update({"retrospective_demo": True, "out_of_sample": False, "leakage_note": "Current model knowledge and web pages may contain the resolved outcome. This is a pipeline trial, not a historical backtest.", "question_metadata": row})
            (output / f"{post['id']}-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            assessment = report.get("assessment") or {}
            row.update({"probability": assessment.get("probability"), "verdict": assessment.get("verdict"), "baseline": report.get("baseline"), "probability_shift": report.get("probability_shift"), "searches_used": report["searches_used"], "pages_opened": len(report["pages"]), "rationale": assessment.get("rationale"), "error": report.get("error") or (None if assessment else "No final assessment")})
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        summary = {"downloaded_at_utc": downloaded_at, "completed_at_utc": utc_now(), "tournament": tournament, "downloaded_questions": len(posts), "retrospective_demo": True, "out_of_sample": False, "submitted_to_metaculus": False, "results": rows}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not rows:
        raise RuntimeError("No historical questions returned")
    return summary


def main() -> None:
    output = Path("snapshots/historical") / utc_now().replace(":", "").replace("+", "_")
    summary = run_sample(os.environ["METACULUS_TOKEN"], os.environ["TAVILY_API_KEY"], os.environ["OPENROUTER_API_KEY"], output, tournament=os.getenv("HISTORICAL_TOURNAMENT", "spring-aib-2026"), research_limit=int(os.getenv("HISTORICAL_LIMIT", "3")))
    if any(row.get("error") for row in summary["results"]):
        raise RuntimeError("Some historical trials failed; inspect saved summary and reports")


if __name__ == "__main__":
    main()
