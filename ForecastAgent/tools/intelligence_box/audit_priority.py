"""Audit the priority-domain pilot without network requests or evidence rewrites."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from .core import now


def audit(root):
    root = Path(root)
    rows = []
    for path in sorted(root.glob("captures/*/capture.json")):
        capture = json.loads(path.read_text(encoding="utf-8"))
        raw_path = capture.get("raw_path")
        raw = (root / raw_path).read_bytes() if raw_path else None
        rows.append({"capture_id": capture["id"], "source": capture["source_id"],
                     "url": capture.get("request_url"), "status": capture["status"],
                     "captured_at_utc": capture["captured_at_utc"],
                     "raw_bytes": len(raw) if raw is not None else None,
                     "hash_matches": hashlib.sha256(raw).hexdigest() == capture.get("raw_sha256") if raw is not None else None,
                     "error": capture.get("error"), "quality": capture.get("quality"),
                     "source_binding": capture.get("source_binding")})
    pilot = json.loads((root / "priority-report.json").read_text(encoding="utf-8"))
    follow = json.loads((root / "priority-followup.json").read_text(encoding="utf-8"))
    replay = json.loads((root / "tesla-replay.json").read_text(encoding="utf-8"))
    ms = json.loads((root / "microsoft-tables.json").read_text(encoding="utf-8"))
    law = json.loads((root / "uk-section-138.json").read_text(encoding="utf-8"))
    original = next(r for r in rows if r["capture_id"] == replay["source_capture_id"])
    return {"schema": "priority_domain_audit_v2", "audited_at_utc": now(),
            "network_requests_during_audit": 0, "model_requests": 0,
            "physical_request_budget": follow["budget"],
            "capture_count": len(rows), "original_status_distribution": dict(Counter(r["status"] for r in rows)),
            "all_available_raw_hashes_match": all(r["hash_matches"] for r in rows if r["raw_bytes"] is not None),
            "configuration_only_cases": [c for c in pilot["cases"] if c["status"] == "configuration_required"],
            "tesla_saved_recovery": {k: replay[k] for k in ("source_capture_id", "raw_sha256", "original_status", "replay_status", "original_capture_unchanged", "http_requests", "model_requests")},
            "recovery_original_still_failed": original["status"] == "failed",
            "tesla_readable_pages": len(replay["records"]),
            "tesla_visual_review": {"pages": [4, 27, 30], "checks": ["Unaudited label", "USD millions and per-share exception", "Q2 2026 column", "Separate GAAP and non-GAAP diluted EPS"], "scope": "Sample financial pages only; no full-document factual certification"},
            "saved_html_table_inspection": {"capture_id": ms["capture_id"], "total_tables": ms["total_tables"], "rows": sum(len(t["rows"]) for t in ms["tables"]), "tables_truncated": ms["tables_truncated"], "network_requests": ms["network_requests"], "units_and_financial_basis_inferred": False},
            "saved_law_lookup": {k: v for k, v in law.items() if k not in {"text", "records", "page_content", "original_xml_excerpt"}},
            "known_gaps": ["Tesla IR index blocked with HTTP 403; original PDF discovered independently on official IR page", "UK bill list timed out; exact bill identity reused from preserved earlier official response", "Bill 4038 publications returned valid empty, not an extraction failure", "PDF structured table extraction is bounded to the first 20 pages; later financial pages retain text and visible extraction gaps", "Rotated PDF text may be incomplete", "SEC adapters are fixture-tested but live requests await SEC_USER_AGENT contact configuration", "Election portals and calendars do not certify winners", "Enacted statute text does not establish later amendments or commencement"],
            "captures": rows, "truth_verification_performed": False,
            "production_integration_complete": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = audit(args.root)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("capture_count", "original_status_distribution", "all_available_raw_hashes_match", "recovery_original_still_failed", "tesla_readable_pages", "physical_request_budget")}))


if __name__ == "__main__":
    main()
