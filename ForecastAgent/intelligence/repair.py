"""Recover observed data resources using remaining independent-supplement slots."""
import copy
import hashlib
from pathlib import Path

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.intelligence.admission import prepare
from ForecastAgent.intelligence.identity import code_identity
from ForecastAgent.intelligence.sources import recovery_plan, read_candidate
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.supplement.stage import fetch_document, now
from ForecastAgent.tavily_research import canonical_url


def recover(bundle, directory, *, prior_manifest, prior_ledger, network=False,
            capture=fetch_document):
    """Reserve before I/O; prior failures and unfinished reservations consume slots.

    This extends an existing supplement stage, not its allowance. No search,
    model, Extract or browser calls are added. Original files remain immutable.
    """
    root, manifest_path, ledger_path = map(Path, (directory, prior_manifest, prior_ledger))
    root.mkdir(parents=True, exist_ok=True)
    if not manifest_path.exists() or not ledger_path.exists():
        report = {"status": "blocked_missing_budget_ledger", "network_calls": 0,
                  "budget_reset": False, "submitted": False}
        save(root / "report.json", report)
        return copy.deepcopy(bundle), report
    # The legacy supplement also holds this directory lock. A concurrent worker
    # cannot change its reservations while the remaining allowance is consumed.
    with task_lock(manifest_path.parent), task_lock(root):
        manifest, prior = load(manifest_path), load(ledger_path)
        ident = str(bundle["request"]["id"])
        limit = manifest.get("http_limit_per_task")
        attempts = prior.get("attempts")
        if (manifest.get("protocol") != "supplement_v1" or ident not in manifest.get("ids", [])
                or str(prior.get("task_id")) != ident or type(limit) is not int
                or limit < 0 or not isinstance(attempts, list)
                or any(a.get("method") not in {"http", "browser", "reparse"} for a in attempts)):
            raise ValueError("Invalid original supplement budget identity")
        used = sum(a["method"] == "http" for a in attempts)
        if used > limit:
            raise ValueError("Original supplement HTTP reservations exceed their cap")
        _, admission = prepare(bundle)
        plan = recovery_plan(bundle, admission)
        frozen = {"protocol": "predictive_data_repair_v1", "task_id": ident,
                  "parent_bundle_sha256": digest(bundle), "code": code_identity(),
                  "prior_manifest_sha256": digest(manifest), "prior_ledger_sha256": digest(prior),
                  "candidate_plan_sha256": digest(plan), "network_enabled": network,
                  "http_limit_shared_with_prior_stage": limit, "prior_http_reservations": used}
        if (root / "identity.json").exists() and load(root / "identity.json") != frozen:
            raise ValueError("Repair inputs, code or shared budget changed; no silent restart")
        save(root / "identity.json", frozen)
        child_path = root / "state.json"
        child = load(child_path) if child_path.exists() else {
            "identity_sha256": digest(frozen), "attempts": [], "captures": {}, "budget_reset": False}
        if child.get("identity_sha256") != digest(frozen):
            raise ValueError("Repair state identity differs")
        own = child["attempts"]
        if len(own) + used > limit or any(a.get("method") != "http" for a in own):
            raise ValueError("Shared supplement HTTP cap exceeded")
        overlay = copy.deepcopy(bundle)
        # Verify and reuse successful captures before considering another URL.
        for url, entry in child["captures"].items():
            path = (root / entry["file"]).resolve()
            if not path.is_relative_to(root.resolve()) or digest(load(path)) != entry["sha256"]:
                raise ValueError("Saved repair capture integrity failure")
            overlay.setdefault("pages", {})[url] = load(path)
        previous_urls = {canonical_url(a["url"]) for a in attempts + own}
        allowed = network and bundle.get("mode", bundle["request"].get("mode")) != "historical_strict"
        for candidate in plan["candidates"]:
            url = candidate["url"]
            if not allowed or canonical_url(url) in previous_urls or used + len(own) >= limit:
                continue
            attempt = {"url": url, "method": "http", "status": "reserved",
                       "started_at_utc": now(), "candidate": candidate}
            own.append(attempt)
            previous_urls.add(canonical_url(url))
            save(child_path, child)
            # Reserved/unknown failures consume their slot, including a process
            # interruption before a response. Never automatically replay them.
            try:
                result = read_candidate(candidate, capture)
                page = result["page"]
                page["body_diagnostics"] = result["body_diagnostics"]
                page["predictive_data_provenance"] = {
                    "parent_bundle_sha256": frozen["parent_bundle_sha256"],
                    "parent_url": candidate["parent_url"], "retrieved_at_utc": page.get("retrieved_at_utc"),
                    "metric_match_verified": False}
                if "structured_view" in result:
                    page["structured_view"] = result["structured_view"]
                name = "captures/" + hashlib.sha256(url.encode()).hexdigest() + ".json"
                save(root / name, page)
                readable = result["body_diagnostics"]["usable_text"]
                attempt.update(status="captured" if readable else "unreadable", file=name,
                               capture_sha256=digest(page), http_audit=page.get("http_audit"))
                child["captures"][url] = {"file": name, "sha256": digest(page), "readable": readable}
                overlay.setdefault("pages", {})[url] = page
            except Exception as exc:
                attempt.update(status="failed", error=type(exc).__name__, detail=str(exc)[:500])
                if getattr(exc, "audit", None):
                    attempt["http_audit"] = exc.audit
            finally:
                attempt["finished_at_utc"] = now()
                save(child_path, child)
        save(child_path, child)
        _, audit = prepare(overlay)
        unresolved = recovery_plan(overlay, audit)
        report = {"status": audit["status"], "admission": audit,
                  "prior_http_reservations": used, "own_http_reservations": len(own),
                  "remaining_http_slots": max(0, limit - used - len(own)),
                  "prior_stage_unchanged": load(ledger_path) == prior,
                  "unknown_reservations": sum(a["status"] == "reserved" for a in own),
                  "remaining_candidates": unresolved["candidates"],
                  "new_search_calls": 0, "new_model_calls": 0, "new_browser_calls": 0,
                  "network_enabled": allowed, "budget_reset": False, "submitted": False}
        save(root / "overlay.json", overlay)
        save(root / "report.json", report)
        return overlay, report
