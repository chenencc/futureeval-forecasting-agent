"""Authoritative JSONL schema and integrity checks shared by writer/readers."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent.parent


class PipelineError(ValueError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(f"{code}: {detail}")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def check(condition, detail):
    if not condition:
        raise ValueError(detail)


def unique(items, key):
    out = {x[key]: x for x in items}
    check(len(out) == len(items), f"Duplicate {key}")
    return out


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def snapshot_payload(record, snapshot):
    sources = {s["source_id"]: s for s in record["evidence"]["sources"]}
    return dict(schema_version="evidence-jsonl/1", question_id=record["question"]["id"],
                question_version_id=snapshot["question_version_id"],
                definition=record["question"]["versions"][snapshot["question_version_id"]]["definition"],
                acquired_by_run_id=snapshot["acquired_by_run_id"], sealed_at=snapshot["sealed_at"],
                evidence_cutoff_at=snapshot["evidence_cutoff_at"], temporal_policy=snapshot["temporal_policy"],
                materials=[dict(binding=m, source=sources[m["source_id"]]) for m in snapshot["materials"]],
                context=snapshot["context"], gaps=snapshot["gaps"])


def validate_record(record, validator=None):
    if validator is None:
        from jsonschema import Draft202012Validator, FormatChecker
        schema = json.loads((ROOT/"docs/QUESTION_JSONL.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(record)
    check(digest({k: v for k, v in record.items() if k != "record_hash"}) == record["record_hash"], "record_hash mismatch")
    q, evidence = record["question"], record["evidence"]
    versions = q["versions"]
    check(q["current_version_id"] in versions, "Unknown current question version")
    for version_id, version in versions.items():
        check(digest(dict(question_id=q["id"], definition=version["definition"])) == version_id, "Question definition hash mismatch")
    check(dt(q["first_observed_at"]) <= dt(q["last_observed_at"]) <= dt(record["updated_at"]), "Question clock order")
    for sha, text in evidence["texts"].items():
        check(hashlib.sha256(text.encode("utf-8")).hexdigest() == sha, "Body hash mismatch")
    sources = unique(evidence["sources"], "source_id")
    searches = unique(record["searches"], "search_id")
    snapshots = unique(evidence["snapshots"], "snapshot_id")
    runs = unique(record["runs"], "run_id")
    for search in searches.values():
        check(search["run_id"] in runs, "Search run missing")
        if search["requested_at"] and search["received_at"]:
            check(dt(search["requested_at"]) <= dt(search["received_at"]), "Search clock order")
        if search["status"] in {"ok", "empty"}:
            check(search["requested_at"] is not None and search["received_at"] is not None, "Successful search clocks missing")
            check((search["status"] == "empty") == (not search["results"]), "Empty search differs from failure")
        if search["status"] == "not_dispatched":
            check(search["requested_at"] is None and search["received_at"] is None, "Invented search dispatch")
        for candidate in search["results"]:
            if candidate.get("text_sha256"):
                check(candidate["text_sha256"] in evidence["texts"], "Candidate body missing")
    for source in sources.values():
        check(source["run_id"] in runs, "Source run missing")
        check(source["final_url"] != source["source_url"], "Redundant redirect URL")
        clocks = [dt(source[k]) for k in ("requested_at", "captured_at", "extracted_at") if source[k] is not None]
        check(clocks == sorted(clocks), "Source clock order")
        if source["status"] == "extracted":
            check(source["text_sha256"] in evidence["texts"], "Extracted body missing")
            check(source["captured_at"] is not None and source["extracted_at"] is not None, "Missing extraction clock")
        else:
            check(source["text_sha256"] is None, "Failed source claims text")
        if source["http_status"] is not None:
            check(source["requested_at"] is not None, "HTTP status without actual request")
        if source["search_id"] is not None:
            check(source["search_id"] in searches, "Search binding missing")
            search = searches[source["search_id"]]
            rank = source["search_result_rank"]
            check(search["run_id"] == source["run_id"] and isinstance(rank, int) and 1 <= rank <= len(search["results"]), "Search rank invalid")
            check(search["results"][rank-1]["url"] == source["source_url"], "Search URL binding mismatch")
        else:
            check(source["search_result_rank"] is None, "Search rank without search ID")
        if source['discovered_via']=='page_link':
            locator=source.get('locator') or {};parent=sources.get(locator.get('parent_source_id'))
            check(parent is not None and parent['run_id']==source['run_id'] and parent['source_id']!=source['source_id'],'Page link parent missing')
            check(any(z.get('url')==source['source_url'] for z in (parent.get('locator') or {}).get('outgoing_links',[])),'Page link URL binding mismatch')
    for snap in snapshots.values():
        check(snap["question_version_id"] in versions and snap["acquired_by_run_id"] in runs, "Snapshot binding missing")
        cutoff = dt(snap["evidence_cutoff_at"])
        check(cutoff <= dt(snap["sealed_at"]), "Cutoff after seal")
        mids = [m["source_id"] for m in snap["materials"]]
        check(len(mids) == len(set(mids)), "Duplicate snapshot material")
        for material in snap["materials"]:
            check(material["source_id"] in sources, "Snapshot source missing")
            source = sources[material["source_id"]]
            for clock in ("requested_at", "captured_at", "extracted_at", "published_at"):
                check(source[clock] is None or dt(source[clock]) <= cutoff, "Evidence after cutoff")
            if material["inclusion"] in {"core","supporting","pending"}:
                check(source["status"] == "extracted", "Unreadable core/supporting material")
        if snap.get("context",{}).get("evidence_admission_policy") in ("evidence-admission-v1","evidence-admission-v2"):
            from .admission import assess,ACCEPTED
            for material in snap['materials']:
                if material['inclusion'] in ACCEPTED:
                    source=sources[material['source_id']]
                    verdict=assess(source,evidence['texts'].get(source.get('text_sha256')),material,cutoff=snap['evidence_cutoff_at'],stored=True,require_kind=snap['context']['evidence_admission_policy']=='evidence-admission-v2')
                    check(verdict==material,'Invalid evidence admission')
        for gap in snap["gaps"]:
            if gap.get("source_id"):
                check(gap["source_id"] in mids, "Gap source unbound")
            if gap.get("search_id"):
                check(gap["search_id"] in searches, "Gap search unbound")
        if snap["temporal_policy"] == "prospective":
            check(dt(versions[snap["question_version_id"]]["observed_at"]) <= cutoff, "Definition after cutoff")
        check(digest(snapshot_payload(record, snap)) == snap["snapshot_id"], "Snapshot hash mismatch")
    for run in runs.values():
        check(run["question_version_id"] in versions, "Run version missing")
        if run["snapshot_id"] is not None:
            check(run["snapshot_id"] in snapshots, "Run snapshot missing")
            check(snapshots[run["snapshot_id"]]["question_version_id"] == run["question_version_id"], "Run snapshot/version mismatch")
        if run["based_on_run_id"] is not None:
            check(run["based_on_run_id"] in runs, "Parent run missing")
            parent = runs[run["based_on_run_id"]]
            check(parent["snapshot_id"] == run["snapshot_id"] and parent["question_version_id"] == run["question_version_id"]
                  and parent["objective"] == run["objective"], "Replay binding changed")
        all_attempts = [a for attempts in run["stages"].values() for a in attempts]
        unique(all_attempts, "attempt_id")
        for stage, attempts in run["stages"].items():
            check(sum(a["status"] in {"queued", "running", "retry_wait", "unknown"} for a in attempts) <= 1, "Multiple active owners")
            for attempt in attempts:
                check(attempt["receipt"] is None or stage == "submission", "Receipt outside submission")
                check(attempt["status"] != "unknown" or stage == "submission", "Unknown state outside submission")
                if attempt["status"] == "running":
                    check(attempt["owner_token"] is not None and attempt["lease_until"] is not None, "Missing owner")
                else:
                    check(attempt["owner_token"] is None and attempt["lease_until"] is None, "Stale owner")
                if attempt["status"] in {"succeeded", "failed", "skipped", "cancelled"}:
                    check(attempt["finished_at"] is not None, "Terminal attempt lacks finish")
                if attempt.get("worker_ready_at"):
                    check(attempt["started_at"] is not None and dt(attempt["worker_ready_at"]) >= dt(attempt["started_at"]), "Invalid worker ready clock")
                if attempt["first_external_request_at"]:
                    check(attempt["started_at"] is not None and dt(attempt["first_external_request_at"]) >= dt(attempt["started_at"]), "Invalid actual dispatch clock")
        configured=run['execution_profile']['parameters'].get('analysis_arms')
        from .workflow import collection_only
        if collection_only(run):
            check(configured == [], 'Collection workflow cannot enable analysis')
            check('decider' not in run['execution_profile']['models'], 'Collection workflow has decision model')
            check(run['selection'] is None and not any(run['results'].values()), 'Collection workflow has forecast')
            check(not any(run['stages'].get(s) for s in ('original','official_graph','selection','submission')),
                  'Collection workflow executed downstream stage')
        if configured is not None:
            check(configured in [['original'],['official_graph','original']] or (collection_only(run) and configured==[]), 'Invalid analysis arms')
            for arm in {'official_graph','original'}-set(configured):
                check(run['results'][arm] is None,'Disabled arm has result')
                check(all(a['status']=='skipped' and a['error_code']=='ANALYSIS_ARM_DISABLED' and a['first_external_request_at'] is None for a in run['stages'].get(arm,[])),'Disabled arm was executed')
        definition = versions[run["question_version_id"]]["definition"]
        for arm, result in run["results"].items():
            if result is None:
                continue
            check(run["snapshot_id"] is not None, "Forecast without snapshot")
            attempts = {a["attempt_id"]: a for a in run["stages"].get(arm, [])}
            check(result["produced_by_attempt_id"] in attempts, "Result attempt missing")
            check(attempts[result["produced_by_attempt_id"]]["status"] == "succeeded", "Result from unsuccessful attempt")
            value = result["prediction"]
            check(run["objective"]["kind"] == "event_outcome", "Market-price output contract not implemented in v1")
            check(value["kind"] == definition["question_type"], "Prediction kind mismatch")
            if value["kind"] == "categorical":
                check(set(value["probabilities"]) == {o["id"] for o in definition["options"]}, "Option IDs mismatch")
                check(abs(sum(value["probabilities"].values())-1) <= 1e-8, "Probabilities do not sum to one")
            if value["kind"] in {"numeric", "date"}:
                scale = definition["scale"]
                check(value["unit"] == scale.get("unit"), "Unit mismatch")
                xs = [p["x"] for p in value["cdf"]]
                ps = [p["p"] for p in value["cdf"]]
                coords = [dt(x) for x in xs] if value["kind"] == "date" else xs
                check(all(a < b for a,b in zip(coords,coords[1:])), "Non-increasing CDF grid")
                check(all(a <= b for a,b in zip(ps,ps[1:])), "Nonmonotone CDF")
                if "grid" in scale:
                    check(xs == scale["grid"], "Incomplete CDF grid")
                else:
                    check(xs[0] == scale.get("lower") and xs[-1] == scale.get("upper"), "Incomplete CDF bounds")
                check(scale.get("open_lower", False) or ps[0] == 0, "Lower tail incomplete")
                check(scale.get("open_upper", False) or ps[-1] == 1, "Upper tail incomplete")
        if run["selection"] is not None:
            expected = "official_graph" if run["results"]["official_graph"] else "original" if run["results"]["original"] else None
            check(run["selection"]["arm"] == expected, "Selection priority violation")
            check(all(run["stages"].get(arm) and run["stages"][arm][-1]["status"] not in {"queued", "running", "retry_wait", "unknown"}
                      for arm in ("official_graph", "original")), "Selection before terminal arms")
    if record["orderbook"]:
        sessions = record["orderbook"]["sessions"]
        unique(sessions, "recording_id")
        for session in sessions:
            check(session["run_id"] in runs, "Book run missing")
            check(dt(session["window_start_at"]) < dt(session["window_end_at"]), "Book window reversed")
            if session["ended_at"]:
                check(dt(session["ended_at"]) >= dt(session["window_start_at"]), "Book end before start")
            sequences = [segment["sequence"] for segment in session["segments"]]
            check(sequences == list(range(len(sequences))), "Book segment sequence gap or duplicate")
            if session["state"] == "complete":
                check(bool(session["ended_at"] and session["first_snapshot_at"] and session["segments"]), "Complete book missing capture or seal")
            for segment in session["segments"]:
                path = PurePosixPath(segment["path"])
                check(not path.is_absolute() and ".." not in path.parts and "\\" not in segment["path"] and ":" not in segment["path"], "Unsafe relative segment path")
    return record


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        check(key not in out, f"Duplicate JSON key: {key}")
        out[key] = value
    return out


def read_jsonl(path):
    data = path.read_bytes()
    check(data.endswith(b"\n"), f"Unterminated JSONL: {path.name}")
    for index, line in enumerate(data.splitlines(), 1):
        check(bool(line.strip()), f"Blank line at {path.name}:{index}")
        yield json.loads(line, object_pairs_hook=_pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"Nonfinite JSON: {x}")))
