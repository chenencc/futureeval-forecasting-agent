"""Inspect originals and build a reversible, deduplicated evidence view."""
import copy
import hashlib
import re

from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.providers.financial import source_urls


def fingerprint(text):
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()


def inspect(page):
    text = page.get("content", "")
    if not isinstance(text, str):
        raise ValueError("Saved content must be text")
    expected = page.get("content_sha256")
    if expected and hashlib.sha256(text.encode()).hexdigest() != expected:
        raise ValueError("Saved body hash mismatch")
    result = body_diagnostics(text, kind=page.get("content_type", ""),
                              metadata=page.get("page_date_metadata"),
                              documents=page.get("documents", []))
    baseline = {"state": result["state"], "usable_text": result["usable_text"]}
    # These are extraction signatures, not topic or outcome classifications.
    residue = re.sub(r"!?\[[^\]\n]*\]\([^\n]*?\)", "", text).strip()
    chrome = re.sub(
        r"Study record managers:\s*refer to (?:the )?Data Element Definitions "
        r"if submitting registration or results information\.?", "", residue, flags=re.I)
    if residue and len(chrome.strip()) < 15 and chrome != residue:
        result.update(state="site_boilerplate_only", usable_text=False)
    elif result["usable_text"] and re.fullmatch(r"[\w .:/-]{1,80}", residue):
        # A site name or domain without a sentence, date or measured value is
        # not a readable article. Correct short announcements remain usable.
        words = residue.split()
        if len(words) <= 3 and not re.search(r"\d", residue) and not page.get("rows"):
            result.update(state="title_only", usable_text=False)
    if "snippet" in str(page.get("capture_method", "")).lower():
        result.update(state="discovery_snippet_only", usable_text=False)
    result.update(schema="predictive_body_admission_v1", content_fingerprint=fingerprint(text),
                  target_coverage="not_assessed", truth_verified=False,
                  baseline_v105_diagnostics=baseline)
    return result


def prepare(bundle):
    """Keep original bytes and coordinates; only the analysis view is filtered."""
    view = copy.deepcopy(bundle)
    pages = bundle.get("pages", {})
    rules = set(source_urls(bundle.get("request", {}).get("resolution_criteria", "")))
    order = sorted(pages, key=lambda url: (url not in rules, url))
    ledger, representatives, admitted = [], {}, {}
    for url in order:
        page = pages[url]
        diagnostic = inspect(page)
        row = {"url": url, "body_sha256": hashlib.sha256(page.get("content", "").encode()).hexdigest(),
               "captured_at_utc": page.get("retrieved_at_utc"), "diagnostics": diagnostic,
               "action": "admit" if diagnostic["usable_text"] else "exclude_body",
               "original_capture_preserved": True, "relevance_verified": False}
        row["saved_usable_text"] = page.get("body_diagnostics", {}).get("usable_text")
        key = diagnostic["content_fingerprint"]
        if diagnostic["usable_text"] and key in representatives:
            row.update(action="duplicate_body", representative_url=representatives[key])
        elif diagnostic["usable_text"]:
            representatives[key] = url
            admitted[url] = copy.deepcopy(page)
            admitted[url]["body_diagnostics"] = diagnostic
        ledger.append(row)
    view["pages"] = admitted
    # Bank entries referring to excluded aliases must never be silently rebound.
    view["excerpts"] = [e for e in view.get("excerpts", []) if e.get("url") in admitted]
    view["evidence"] = [e for e in view.get("evidence", []) if e.get("url") in admitted]
    gaps = list(view.get("gaps", view.get("result", {}).get("gaps", [])))
    gaps.extend({"code": "body_not_admitted", "url": r["url"],
                 "body_state": r["diagnostics"]["state"]}
                for r in ledger if r["action"] == "exclude_body")
    view["gaps"] = gaps
    audit = {"schema": "predictive_admission_audit_v1", "sources": ledger,
             "saved_body_count": len(pages), "admitted_distinct_bodies": len(admitted),
             "excluded_bodies": sum(r["action"] == "exclude_body" for r in ledger),
             "duplicate_bodies": sum(r["action"] == "duplicate_body" for r in ledger),
             "status": "ready_with_gaps" if admitted else "needs_material_recovery",
             "raw_ledgers_unchanged": True, "budget_reset": False, "network_calls": 0}
    view["predictive_admission"] = audit
    return view, audit


def unique_information(first, second):
    """Gate rereading by distinct text, not by different URLs or coordinates."""
    known = {fingerprint(s["text"]) for s in first.get("evidence", [])}
    novel, duplicates = [], []
    for span in second.get("evidence", []):
        key = fingerprint(span["text"])
        if key in known:
            duplicates.append(span["evidence_id"])
        else:
            known.add(key)
            novel.append(span)
    return {"new_distinct_chars": sum(len(s["text"]) for s in novel),
            "new_ids": [s["evidence_id"] for s in novel], "duplicate_ids": duplicates,
            "semantic_information_gain_verified": False}
