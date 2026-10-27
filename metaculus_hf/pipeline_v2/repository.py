"""Single-writer, revision-fenced, crash-safe local question Parquet repository."""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import time
from .contracts import PipelineError, canonical, digest, now, read_jsonl, validate_record


def ensure(condition, code, detail=""):
    if not condition:
        raise PipelineError(code, detail)


@contextmanager
def file_lock(path, timeout=15):
    """OS ownership, automatically released on process death; never unlink lock files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic()+timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                ensure(time.monotonic() < deadline, "LOCK_TIMEOUT", path.name)
                time.sleep(.025)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_bytes(path, data, *, before_replace=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".uncommitted-", dir=path.parent)
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if before_replace:
            before_replace()
        # Short retry only for transient Windows reader/antivirus share locks, never provider calls.
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                published = True
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.025)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError as exc:
        if published:
            raise PipelineError("COMMIT_UNCERTAIN", "read and reconcile the committed revision") from exc
        raise
    finally:
        if not published and os.path.exists(temporary):
            os.unlink(temporary)  # Only this unsuccessful write's temp; no persisted-data deletion API.


def unchanged_prefix(old, new, code):
    ensure(len(new) >= len(old) and old == new[:len(old)], code)


def validate_transition(old, new):
    ensure(new["question"]["id"] == old["question"]["id"], "QUESTION_ID_CHANGED")
    for key, value in old["question"]["versions"].items():
        ensure(new["question"]["versions"].get(key) == value, "FROZEN_DEFINITION_CHANGED")
    for key, value in old["evidence"]["texts"].items():
        ensure(new["evidence"]["texts"].get(key) == value, "PERSISTED_TEXT_CHANGED")
    for key in ("sources", "snapshots"):
        unchanged_prefix(old["evidence"][key], new["evidence"][key], "FROZEN_EVIDENCE_CHANGED")
    unchanged_prefix(old["searches"], new["searches"], "SEARCH_HISTORY_CHANGED")
    if old["orderbook"] is not None:
        ensure(new["orderbook"] is not None, "BOOK_HISTORY_REMOVED")
        prior = old["orderbook"]["sessions"]
        following = new["orderbook"]["sessions"]
        ensure(len(following) >= len(prior), "BOOK_HISTORY_REMOVED")
        for previous, current in zip(prior, following):
            for key in ("recording_id", "run_id", "window_start_at", "window_end_at"):
                ensure(previous[key] == current[key], "BOOK_WINDOW_IDENTITY_CHANGED")
            if previous["state"] not in {"starting", "recording"}:
                ensure(previous == current, "SEALED_BOOK_CHANGED")
    ensure(len(new["runs"]) >= len(old["runs"]), "RUN_HISTORY_REMOVED")
    for index, previous in enumerate(old["runs"]):
        current = new["runs"][index]
        immutable = set(previous) - {"stages", "results", "selection", "snapshot_id"}
        ensure(all(current[k] == previous[k] for k in immutable), "RUN_IDENTITY_CHANGED")
        if previous["snapshot_id"] is not None:
            ensure(current["snapshot_id"] == previous["snapshot_id"], "RUN_SNAPSHOT_CHANGED")
        if previous["selection"] is not None:
            ensure(current["selection"] == previous["selection"] and current["results"] == previous["results"], "FINAL_SELECTION_CHANGED")
        for arm in ("official_graph", "original"):
            if previous["results"][arm] is not None:
                ensure(current["results"][arm] == previous["results"][arm], "FINAL_RESULT_CHANGED")
        for stage, attempts in previous["stages"].items():
            updated = current["stages"].get(stage, [])
            ensure(len(updated) >= len(attempts), "ATTEMPT_HISTORY_REMOVED")
            for attempt, next_attempt in zip(attempts, updated):
                ensure(attempt["attempt_id"] == next_attempt["attempt_id"] and attempt["queued_at"] == next_attempt["queued_at"], "ATTEMPT_IDENTITY_CHANGED")
                if attempt["status"] in {"succeeded", "failed", "skipped", "cancelled"}:
                    ensure(next_attempt == attempt, "TERMINAL_ATTEMPT_CHANGED")
                if attempt["status"] == "unknown":
                    ensure(next_attempt["status"] in {"unknown", "succeeded", "failed"}, "AMBIGUOUS_SUBMISSION_RETRIED")
                if attempt["status"] == "running":
                    ensure(next_attempt["status"] != "queued", "ACTIVE_ATTEMPT_RESET")
                    if next_attempt["status"] == "running":
                        ensure(next_attempt["owner_token"] == attempt["owner_token"], "LIVE_OWNER_REPLACED")
                if attempt.get("worker_ready_at") is not None:
                    ensure(next_attempt.get("worker_ready_at") == attempt["worker_ready_at"], "WORKER_READY_CLOCK_CHANGED")
                if attempt["first_external_request_at"] is not None:
                    ensure(next_attempt["first_external_request_at"] == attempt["first_external_request_at"], "REQUEST_CLOCK_CHANGED")


class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        from .parquet_store import warm_storage
        warm_storage()
        from jsonschema import Draft202012Validator, FormatChecker
        schema_path = Path(__file__).resolve().parent.parent/"docs/QUESTION_JSONL.schema.json"
        self.contract = json.loads(schema_path.read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(self.contract, format_checker=FormatChecker())

    def paths(self, question_id):
        pieces = question_id.split(":", 1)
        ensure(len(pieces) == 2 and pieces[0] in {"polymarket", "metaculus"} and bool(pieces[1]), "INVALID_QUESTION_ID")
        ensure(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", pieces[1])) and pieces[1] not in {".", ".."}, "UNSAFE_QUESTION_ID")
        key = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
        path = self.root / pieces[0] / pieces[1] / "question.parquet"
        legacy_paths=[self.root/pieces[0]/pieces[1]/"question.jsonl",self.root/"working"/pieces[0]/f"{key}.jsonl"]
        split=path.with_name('state.json')
        existing=[p for p in [path,split,*legacy_paths] if p.exists()]
        ensure(len(existing)<=1,"QUESTION_STORAGE_CONFLICT")
        if existing:path=existing[0]
        elif getattr(self,'split_new',False) or (self.root/'control/storage.json').exists():
            if not getattr(self,'split_new',False):
                ensure(json.loads((self.root/'control/storage.json').read_text()) == {'format':'split-question/2'},'STORAGE_CONFIGURATION_INVALID')
            path=split
        return path, self.root/".locks"/f"{key}.lock"

    def _read(self, path):
        ensure(path.is_file(), "QUESTION_NOT_FOUND")
        if path.name=='state.json':
            from .split_repository import read_full
            return read_full(self,path)
        try:
            if path.suffix==".parquet":
                from .parquet_store import read_questions
                rows=read_questions(path)
            else:rows=list(read_jsonl(path))
            ensure(len(rows) == 1, "INVALID_WORKING_FILE")
            return validate_record(rows[0], self.validator)
        except (ValueError, KeyError) as exc:
            if isinstance(exc, PipelineError):
                raise
            raise PipelineError("RECORD_INTEGRITY_FAILURE", str(path)) from exc

    def get_question(self, question_id):
        path, lock = self.paths(question_id)
        # Readers join the tiny write lock for Windows sharing semantics and consistent integrity checks.
        with file_lock(lock):
            record = self._read(path)
        ensure(record["question"]["id"] == question_id, "QUESTION_ID_MISMATCH")
        return record

    def encoded(self,path,records):
        if path.suffix==".parquet":
            from .parquet_store import question_bytes
            return question_bytes(records,self.contract)
        return ("".join(canonical(r)+"\n" for r in records)).encode("utf-8")

    def _save(self,path,record,before_replace=None):
        if path.name=='state.json':
            from .split_repository import write_full
            write_full(self,path,record,before_replace)
        else:atomic_bytes(path,self.encoded(path,[record]),before_replace=before_replace)

    def get_runtime(self,question_id):
        path,lock=self.paths(question_id)
        if path.name!='state.json':return self.get_question(question_id)
        from .split_repository import runtime
        with file_lock(lock):record=runtime(self,path)
        ensure(record['question']['id']==question_id,'QUESTION_ID_MISMATCH')
        return record

    def commit_book(self,question_id,update):
        path,lock=self.paths(question_id)
        if path.name!='state.json':return self.commit_patch(question_id,None,update)
        from .split_repository import book_commit
        with file_lock(lock):return book_commit(self,path,update)

    def create(self, record):
        record = deepcopy(record)
        ensure(record["revision"] == 1, "INITIAL_REVISION_REQUIRED")
        record["record_hash"] = digest({k: v for k, v in record.items() if k != "record_hash"})
        validate_record(record, self.validator)
        path, lock = self.paths(record["question"]["id"])
        with file_lock(lock):
            ensure(not path.exists(), "QUESTION_EXISTS")
            self._save(path,record)
        return record

    def commit_patch(self, question_id, expected_revision, update, *, before_replace=None):
        """Merge against the current record under ownership, never overwrite from a stale callback.

        update is a trusted core function, not arbitrary code supplied by models.
        None revision is for in-core semantic operations whose own attempt token is checked.
        """
        path, lock = self.paths(question_id)
        with file_lock(lock):
            old = self._read(path)
            if expected_revision is not None:
                ensure(expected_revision == old["revision"], "REVISION_CONFLICT")
            new = deepcopy(old)
            update(new)
            new["revision"] = old["revision"]+1
            new["updated_at"] = now()
            validate_transition(old, new)
            new["record_hash"] = digest({k: v for k, v in new.items() if k != "record_hash"})
            validate_record(new, self.validator)
            self._save(path,new,before_replace)
        return new

    def read_snapshot(self, question_id, snapshot_id):
        record = self.get_question(question_id)
        snaps = [s for s in record["evidence"]["snapshots"] if s["snapshot_id"] == snapshot_id]
        ensure(len(snaps) == 1, "SNAPSHOT_NOT_FOUND")
        from .contracts import snapshot_payload
        payload = snapshot_payload(record, snaps[0])
        for material in payload["materials"]:
            sha = material["source"]["text_sha256"]
            material["text"] = record["evidence"]["texts"].get(sha) if sha else None
        return {"snapshot_id": snapshot_id, **payload}

    def export_shard(self, question_ids):
        """Local immutable export only; no remote upload or cleanup side effects."""
        records = [self.get_question(qid) for qid in sorted(set(question_ids))]
        ensure(bool(records), "EMPTY_SHARD")
        for record in records:
            for run in record["runs"]:
                ensure(not any(a["status"] in {"queued", "running", "retry_wait"} for aa in run["stages"].values() for a in aa), "WORK_IN_PROGRESS")
            if record["orderbook"]:
                ensure(all(s["state"] not in {"starting", "recording"} for s in record["orderbook"]["sessions"]), "BOOK_IN_PROGRESS")
        plain = ("".join(canonical(record)+"\n" for record in records)).encode("utf-8")
        from .parquet_store import question_bytes
        compressed = question_bytes(records,self.contract)
        sha = hashlib.sha256(compressed).hexdigest()
        path = self.root/"archive"/"questions"/f"part-{sha}.parquet"
        with file_lock(self.root/".locks"/"export.lock"):
            if path.exists():
                ensure(path.read_bytes() == compressed, "ARCHIVE_COLLISION")
            else:
                atomic_bytes(path, compressed)
            manifest = dict(schema_version="question-shard/1", path=path.relative_to(self.root).as_posix(),
                            sha256=sha, plain_sha256=hashlib.sha256(plain).hexdigest(), bytes=len(compressed),
                            plain_bytes=len(plain), records=len(records), record_schema="forecast-question-jsonl/1",codec="parquet-zstd")
            mpath=self.root/"archive"/"manifests"/f"{sha}.json"
            if not mpath.exists():
                atomic_bytes(mpath,(canonical(manifest)+"\n").encode("utf-8"))
        return manifest
