"""Export captured material without a truth verdict or a forecast."""
import json
from pathlib import Path
from ForecastAgent.tavily_research import canonical_url


def intelligence_package(bundle):
    sources = dict(bundle.get("source_leads", {}))
    for search in bundle.get("searches", []):
        for hit in search.get("results", []):
            sources.setdefault(canonical_url(hit["url"]), hit)
    return {"schema": "intelligence_package_v1", "request": bundle["request"],
            "request_hash": bundle["request_hash"], "mode": bundle["mode"],
            "collection_result": bundle.get("result"), "plan": bundle.get("plan"),
            "sources": list(sources.values()), "searches": bundle.get("searches", []),
            "pages": bundle.get("pages", {}), "excerpts": bundle.get("excerpts", []),
            "fetch_attempts": bundle.get("fetch_attempts", []), "extract_attempts": bundle.get("extract_attempts", []),
            "quarantine": bundle.get("quarantine", []), "channel_catalog": bundle.get("channel_catalog"),
            "truth_verified": False, "out_of_sample": False,
            "limitations": ["Quotes are located in saved text, not independently fact-checked.",
                            bundle.get("temporal_warning", "Historical availability must be established separately.")],
            "resources": {"tavily_basic_attempts": len(bundle.get("searches", [])),
                          "free_fetch_attempts": len(bundle.get("fetch_attempts", [])),
                          "extract_batches": len(bundle.get("extract_attempts", []))}}


def export_intelligence(bundle, directory):
    path = Path(directory) / "intelligence.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(intelligence_package(bundle), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return str(path)
