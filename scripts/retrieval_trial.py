"""Five fixed historical retrieval tasks, with durable per-question budgets."""
import json
import os
from pathlib import Path

from scripts.retrieval_agent import run_retrieval


def main():
    root = Path("snapshots/retrieval-five")
    root.mkdir(parents=True, exist_ok=True)
    questions = json.loads(Path("fixtures/retrieval_2026_five.json").read_text(encoding="utf-8"))
    rows = []
    for question in questions:
        ident = question["id"]
        print(f"START {ident}: {question['question']}", flush=True)
        try:
            bundle = run_retrieval(question, root / ident, os.environ["TAVILY_API_KEY"], os.environ["OPENROUTER_API_KEY"])
            coverage = bundle["result"].get("coverage", [])
            critical = [n for n in coverage if n["priority"] == "critical"]
            row = {"id": ident, "question": question["question"], "status": bundle["result"]["status"],
                   "searches": len(bundle["searches"]), "pages": len(bundle["pages"]), "evidence": len(bundle["evidence"]),
                   "critical_covered": sum(bool(n["evidence_ids"]) for n in critical), "critical_total": len(critical),
                   "quarantined": len(bundle["quarantine"]), "summary": bundle["result"]["summary"],
                   "gaps": bundle["result"].get("gaps", []), "conflicts": bundle["result"].get("conflicts", []),
                   "last_error": bundle.get("last_error"), "last_error_detail": bundle.get("last_error_detail")}
        except Exception as exc:
            row = {"id": ident, "question": question["question"], "status": "failed", "error": str(exc)[:500]}
        rows.append(row)
        (root / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(row, ensure_ascii=True), flush=True)
    if any(r["status"] == "failed" for r in rows):
        raise RuntimeError("One or more retrieval tasks failed; inspect saved bundles")


if __name__ == "__main__":
    main()
