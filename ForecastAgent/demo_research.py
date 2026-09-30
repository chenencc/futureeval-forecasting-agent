"""Run one previously collected question through the read-only Ultra agent."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.monitor_tournament import API_ROOT, get_json
from ForecastAgent.ultra_research_agent import run_research


def main() -> None:
    item = json.loads(Path("data/resolved_sample_metadata.json").read_text(encoding="utf-8"))["items"][1]
    detail = None
    detail_error = None
    try:
        detail = get_json(f"{API_ROOT}{item['post_id']}/", os.environ["METACULUS_TOKEN"])
    except Exception as exc:
        detail_error = f"{type(exc).__name__}: {exc}"
    question_data = (detail or {}).get("question") or {}
    criteria = question_data.get("resolution_criteria") or (detail or {}).get("resolution_criteria") or "Unavailable; use the question wording only."
    fine_print = question_data.get("fine_print") or (detail or {}).get("fine_print") or ""

    report = run_research(
        item["title"], criteria, fine_print,
        os.environ["TAVILY_API_KEY"], os.environ["OPENROUTER_API_KEY"],
    )
    report["question_metadata"] = item
    report["question_detail_error"] = detail_error
    report["retrospective_demo"] = True

    now = datetime.now(timezone.utc)
    output = Path("snapshots/demo") / now.strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=True)
    path = output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"READ-ONLY ULTRA DEMO: {item['title']}")
    print(f"Tavily basic searches: {report['searches_used']}/{report['search_budget']}; pages opened: {len(report['pages'])}")
    for search in report["searches"]:
        print(f"SEARCH [{search.get('purpose', 'gap')}]: {search['query']} ({len(search['results'])} new results)")
    for page in report["pages"]:
        print(f"FETCHED: {page['url']} ({len(page['content'])} chars)")
    print("FINAL ASSESSMENT:")
    print("Frozen baseline:", json.dumps(report["baseline"], ensure_ascii=False))
    print("Probability shift:", report["probability_shift"])
    print(json.dumps(report["assessment"], ensure_ascii=False, indent=2))
    print(f"Saved report to {path}")
    if report["error"] or report["assessment"] is None:
        raise RuntimeError(report["error"] or "No final assessment")


if __name__ == "__main__":
    main()
