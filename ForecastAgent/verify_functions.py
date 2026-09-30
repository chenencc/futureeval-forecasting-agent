"""Manual live smoke checks with durable budgets; no forecasting or submission."""
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import unittest

from ForecastAgent.providers.ultra import ask_ultra, fetch_public_page, utc_now
from ForecastAgent.providers.financial import fetch_structured
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.skill_loader import freeze_skills, load_skill
from ForecastAgent.tools.registry import TOOLS

ROOT = Path("snapshots/function-verification")


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    report = {"at": utc_now(), "commit": os.environ.get("GITHUB_SHA"), "checks": []}
    output = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / "tests"))
    with contextlib.redirect_stdout(output):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    (ROOT / "offline-tests.txt").write_text(output.getvalue(), encoding="utf-8")
    report["offline"] = {"tests": result.testsRun, "passed": result.wasSuccessful(),
                         "failures": len(result.failures), "errors": len(result.errors)}
    key = os.environ.get("TAVILY_API_KEY", "")
    router = os.environ.get("OPENROUTER_API_KEY", "")

    def check(name, function):
        try:
            details = function()
            row = {"name": name, "status": "passed", "details": details}
        except Exception as exc:
            message = str(exc)
            for secret in (key, router):
                if secret:
                    message = message.replace(secret, "[REDACTED]")
            row = {"name": name, "status": "failed", "error": f"{type(exc).__name__}: {message[:500]}"}
        report["checks"].append(row)
        (ROOT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(row, ensure_ascii=True), flush=True)

    probes = [
        ("html", "https://example.org/"),
        ("pdf", "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"),
        ("json", "https://api.github.com/repos/Metaculus/forecasting-tools"),
    ]
    def read_probe(name, url):
        page = fetch_public_page(url)
        assert page["content"] and page["documents"] and page["raw_response_base64"]
        (ROOT / f"{name}.json").write_text(json.dumps(page), encoding="utf-8")
        return {"url": url, "format": page["content_type"], "documents": page["document_count"], "sha256": page["sha256"]}
    for name, url in probes:
        check(name, lambda n=name, u=url: read_probe(n, u))
    cutoff = datetime(2026, 8, 20, tzinfo=timezone.utc)
    def finance_probe(name, url):
        def capture(endpoint):
            response = fetch_public_page(endpoint)
            (ROOT / f"{name}-raw.json").write_text(json.dumps(response), encoding="utf-8")
            return response
        page = fetch_structured(url, cutoff, capture)
        assert page and page["rows"] and all(row["date"] < "2026-08-20" for row in page["rows"])
        (ROOT / f"{name}.json").write_text(json.dumps(page), encoding="utf-8")
        return {"rows": len(page["rows"]), "latest": page["rows"][-1]["date"], "endpoint": page["data_endpoint"]}
    check("yahoo", lambda: finance_probe("yahoo", "https://finance.yahoo.com/quote/%5ETYX/history"))
    check("alfred_csv", lambda: finance_probe("alfred", "https://fred.stlouisfed.org/series/DGS30"))

    request = {"question": "Verify pypdf documentation discovery and exact source reading.",
               "resolution_criteria": "Identify official documentation for PDF text extraction; no forecast.", "mode": "live"}
    task_dir = ROOT / "search-task"; task_dir.mkdir(exist_ok=True)
    task = RetrievalTask(task_dir, request)
    if task.bundle["plan"] is None:
        task.execute("plan_evidence", {"needs": [{"id": "docs", "condition": "PDF text extraction documentation",
                     "priority": "critical", "expected_source": "pypdf", "query": "pypdf text extraction"}]}, key)
        task.save()
    def search_probe():
        if not task.bundle["searches"]:
            task.execute("search_tavily", {"query": "pypdf official documentation extract text", "need_ids": ["docs"],
                         "reason": "Verify one basic search for the primary documentation", "topic": "general",
                         "include_domains": ["pypdf.readthedocs.io"], "include_domains_mode": "restrict", "exact_match": False}, key)
        attempt = task.bundle["searches"][0]
        if attempt["status"] != "completed":
            raise RuntimeError("Existing search failed or was interrupted; refusing automatic retry")
        return {"attempts": len(task.bundle["searches"]), "hits": len(attempt["results"])}
    check("tavily_basic", search_probe)
    hits = [h for s in task.bundle["searches"] for h in s["results"]]
    if hits:
        url = hits[0]["url"]
        check("searched_page_free_fetch", lambda: task.execute("fetch_page", {"url": url}, key))
        task.save()
        if task.rescue_candidates():
            check("basic_extract", lambda: task.execute("extract_failed_pages", {"urls": task.rescue_candidates()[:1],
                        "need_ids": ["docs"], "reason": "Primary documentation failed actual free retrieval"}, key))
        else:
            report["checks"].append({"name": "basic_extract", "status": "skipped", "reason": "Free read succeeded; rescue not eligible. Offline tests validate request and budget."})

    freeze_skills(task.bundle)
    def ultra_probe():
        prior = task.bundle.get("ultra_verification")
        if prior:
            if prior["status"] != "passed":
                raise RuntimeError("Existing Ultra verification interrupted/failed; no automatic new logical call")
            return prior
        task.bundle["ultra_verification"] = {"status": "reserved", "at": utc_now()}; task.save()
        try:
            tool = next(t for t in TOOLS if t["function"]["name"] == "load_research_skill")
            message = ask_ultra([{"role": "system", "content": "Verify tool calling only. Do not forecast or search."},
                                 {"role": "user", "content": "Call load_research_skill with name evidence-review."}],
                                router, tools=[tool], forced_tool="load_research_skill")
            calls = message.get("tool_calls") or []
            assert calls and calls[0]["function"]["name"] == "load_research_skill"
            args = json.loads(calls[0]["function"]["arguments"])
            skill = load_skill(task.bundle, args["name"])
            assert skill["name"] == "evidence-review"
            task.bundle["ultra_verification"] = {"status": "passed", "skill": skill["name"], "sha256": skill["sha256"]}
            return task.bundle["ultra_verification"]
        finally:
            task.save()
    check("ultra_tool_and_skill", ultra_probe)
    task.save()
    report["resources"] = {"basic_searches": len(task.bundle["searches"]), "extract_batches": len(task.bundle["extract_attempts"])}
    report["submitted"] = False
    (ROOT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result.wasSuccessful() or any(row["status"] == "failed" for row in report["checks"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
