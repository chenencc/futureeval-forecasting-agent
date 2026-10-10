"""Composable acquisition view and bounded frozen-snapshot analysis experiment."""
import argparse
import copy
import hashlib
import json
import zipfile
from collections import Counter
from contextlib import nullcontext
from pathlib import Path

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.intelligence import VERSION
from ForecastAgent.intelligence.admission import prepare
from ForecastAgent.intelligence.requirements import contract, classify_need
from ForecastAgent.intelligence.sources import recovery_plan
from ForecastAgent.intelligence.identity import code_identity
from ForecastAgent.runtime.task_lock import task_lock


def prepare_package(bundle, directory, clock_utc):
    """Freeze a derivative package. Raw captures, journals and quotas stay intact."""
    directory = Path(directory)
    requirements = contract(bundle["request"], clock_utc=clock_utc)
    requirements["planned_need_observability"] = [
        {"id": need.get("id"), "condition": need.get("condition"),
         **classify_need(need, clock_utc)} for need in bundle.get("plan", [])]
    identity = {"protocol": VERSION, "original_bundle_sha256": digest(bundle),
                "requirements_sha256": digest(requirements), "code": code_identity(), "budget_reset": False}
    if (directory / "identity.json").exists() and load(directory / "identity.json") != identity:
        raise ValueError("Frozen intelligence package identity changed")
    save(directory / "identity.json", identity)
    view, admission = prepare(bundle)
    plan = recovery_plan(bundle, admission)
    save(directory / "requirements.json", requirements)
    save(directory / "admission.json", admission)
    save(directory / "source-recovery-plan.json", plan)
    save(directory / "analysis-view.json", view)
    from ForecastAgent.intelligence.research_map import project
    map_projection = project(bundle, view, directory / "research-map")
    report = {"protocol": VERSION, "status": admission["status"],
              "admission": admission, "source_recovery_candidates": len(plan["candidates"]),
              "original_bundle_sha256": digest(bundle), "analysis_view_sha256": digest(view),
              "new_provider_calls": 0, "submitted": False, "budget_reset": False,
              "research_map": map_projection}
    save(directory / "report.json", report)
    return view, report


def collect(request, directory, *, clock_utc, recover_data=True, research_map=False, channel_tools=False):
    """Run the existing complete collector/supplement chain with explicit inputs.

    This is an acquisition-only entry point. The contract is trusted operator
    input to the existing agent; it does not reinterpret platform resolution rules.
    Free capture, search, Extract and retry caps continue to belong to that chain.
    The optional data extension shares its remaining supplement HTTP slots.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with task_lock(directory):
        if channel_tools:
            from ForecastAgent.channels.native import FIELD, POLICY
            request = copy.deepcopy(request)
            request[FIELD] = POLICY
        if research_map:
            from ForecastAgent.intelligence.research_map import enable
            request = enable(request)
        return _collect(request, directory, clock_utc=clock_utc, recover_data=recover_data)


def _collect(request, directory, *, clock_utc, recover_data=True):
    from ForecastAgent.releases import v1_0_5
    from ForecastAgent.research_loop import post_supplement
    import time
    original = copy.deepcopy(request)
    if post_supplement.enabled(original):
        original[post_supplement.STAGE_FIELD] = 'intelligence_after_data_recovery'
    original["predictive_information_contract"] = contract(request, clock_utc=clock_utc)
    root = Path(directory)
    from ForecastAgent.channels.contracts import FIELD as channels_field, POLICY as channels_policy
    candidate = original.get(channels_field) == channels_policy
    if candidate:
        from ForecastAgent.intelligence.development_collection import collect as collect_candidate
        source_root = root / 'retrieval/development-channels'
        result = collect_candidate(original, source_root)
        if result['status'] != 'complete':
            save(root / 'collection-status.json', result)
            return result
        package = result['package']
    else:
        source_root = root / 'retrieval/release-1.0.5'
        adapter = v1_0_5.collect(original, root / "retrieval")
        if not adapter.get("release_acquisition"):
            save(root / "collection-status.json", {"status": "collection_incomplete",
                 "collector_result": adapter.get("result"), "analysis_started": False,
                 "budget_reset": False, "submitted": False})
            return adapter
        package = v1_0_5.supplement(adapter, root, original["id"])
    # Accept a path adapter as well as the current dictionary adapter.
    if isinstance(package, (str, Path)):
        package = load(package)
    recovery = None
    if recover_data:
        from ForecastAgent.intelligence.repair import recover
        prior = source_root / 'supplement'
        package, recovery = recover(package, root / "data-recovery",
            prior_manifest=prior / "manifest.json",
            prior_ledger=prior / "tasks" / str(original["id"]) / "supplement.json", network=True)
    post_report = None
    if post_supplement.enabled(original):
        reservation = load(source_root / 'map-reservation.json')
        pipeline_state = load(source_root / 'state.json')
        if pipeline_state.get('post_reservation_sha256') != digest(reservation):
            raise ValueError('Final map reservation integrity mismatch')
        limits = reservation['limits']
        deadline = time.monotonic() + max(0, reservation['started_at_epoch']
            + limits['seconds_remaining'] - time.time())
        route = nullcontext()
        if candidate:
            from ForecastAgent.intelligence.development_collection import model_route, DEFAULT_MODEL
            route = model_route(original.get('collection_model', DEFAULT_MODEL))
        with route:
            package, post_report = post_supplement.run(package, root / 'final-map-review',
                http_cap=post_supplement.remaining_http(package, limits),
                failure_cap=post_supplement.remaining_failures(package, limits), deadline=deadline)
        save(root / 'final-map-package.json', package)
    view, report = prepare_package(package, root / "intelligence", clock_utc)
    report["data_recovery"] = recovery
    report['post_supplement_map'] = post_report
    from ForecastAgent.research_loop import dispatch
    if dispatch.enabled(package):
        from types import SimpleNamespace
        report['material_processing'] = dispatch.status(SimpleNamespace(bundle=package, cutoff=None))
        report['package_exported'] = True
    save(root / "intelligence/report.json", report)
    return {"view": view, "report": report, "analysis_started": False, "submitted": False}


def replay(audit_path, archive_root, output, *, model_ids=()):
    from ForecastAgent.intelligence.decision import compare
    audit = load(audit_path)
    output = Path(output)
    ids = [r["question_id"] for r in audit["rows"]]
    if not set(model_ids) <= set(ids) or len(model_ids) > 3:
        raise ValueError("Use at most three unique preserved production cases for this model pilot")
    if len(set(model_ids)) != len(model_ids):
        raise ValueError("Duplicate model case IDs")
    selection = {"protocol": VERSION, "audit_sha256": digest(audit),
                 "ids": ids, "model_ids": list(model_ids), "physical_http_cap": 3 * len(model_ids)}
    if (output / "selection.json").exists() and load(output / "selection.json") != selection:
        raise ValueError("Frozen replay cohort changed")
    save(output / "selection.json", selection)
    rows = []
    for row in audit["rows"]:
        ident, sha = row["question_id"], row["archive_sha256"]
        archive = Path(archive_root) / sha[:2] / (sha + ".zip")
        if hashlib.sha256(archive.read_bytes()).hexdigest() != sha:
            raise ValueError("Original archive checksum mismatch")
        with zipfile.ZipFile(archive) as z:
            path = f"tasks/{ident}/analysis-input.json"
            if path not in z.namelist():
                paths = [n for n in z.namelist() if n.startswith(f"tasks/{ident}/retrieval/") and n.endswith("bundle.json")]
                path = min(paths, key=len)
            bundle = json.loads(z.read(path))
        root = output / ident
        clock = row["receipt"]["confirmed_at_utc"]
        view, report = prepare_package(bundle, root, clock)
        record = {"question_id": ident, "archive_sha256": sha,
                  "clock_utc": clock, "admission": report["admission"],
                  "source_recovery_candidates": report["source_recovery_candidates"]}
        if report["status"] == "needs_material_recovery":
            record["analysis"] = {"status": "needs_material_recovery", "no_default_probability": True,
                                  "new_model_calls": 0, "submitted": False}
        else:
            try:
                record["analysis"] = compare(view, root / "decision", clock,
                                               run_models=ident in model_ids)
            except (ValueError, RuntimeError) as exc:
                record["analysis"] = {"status": "failed", "error": str(exc),
                                      "state_preserved": True, "submitted": False}
        rows.append(record)
        save(output / "progress.json", {"rows": rows, "completed": len(rows), "total": len(ids)})
        print(json.dumps({"question_id": ident, "admission_status": report["status"],
                          "admitted": report["admission"]["admitted_distinct_bodies"],
                          "excluded": report["admission"]["excluded_bodies"],
                          "duplicates": report["admission"]["duplicate_bodies"],
                          "analysis_status": record["analysis"]["status"]}), flush=True)
    summary = {"protocol": VERSION, "rows": rows,
               "state_distribution": dict(Counter(r["analysis"]["status"] for r in rows)),
               "saved_bodies": sum(r["admission"]["saved_body_count"] for r in rows),
               "admitted_distinct_bodies": sum(r["admission"]["admitted_distinct_bodies"] for r in rows),
               "excluded_bodies": sum(r["admission"]["excluded_bodies"] for r in rows),
               "duplicate_bodies": sum(r["admission"]["duplicate_bodies"] for r in rows),
               "actual_model_http_records": len(list(output.glob("*/decision/*/http/*.json"))),
               "new_search_calls": 0, "new_capture_calls": 0, "submitted": False,
               "accuracy_available": False, "brier": None,
               "limitations": ["Existing production snapshots have different release versions.",
                  "The paired model arms share identical original spans; the brief is a derived hypothesis.",
                  "Admission improvements do not prove new-source recall or forecasting accuracy.",
                  "No fresh material is mixed into historical submission-time packets."]}
    save(output / "report.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Predictive intelligence snapshot replay")
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-ids", nargs="*", default=[])
    args = parser.parse_args()
    replay(args.audit, args.archives, args.output, model_ids=args.model_ids)


if __name__ == "__main__":
    main()
