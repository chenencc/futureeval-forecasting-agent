"""Point-in-time, per-question records for forecast and market research."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from hashlib import sha256
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return str(value)


class SnapshotStore:
    def __init__(self, root: str | Path = "snapshots", *, run_mode: str, publish_requested: bool) -> None:
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        self.directory = Path(root) / self.run_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.run_mode = run_mode
        self.publish_requested = publish_requested
        self.commit = os.environ.get("GITHUB_SHA") or self._git_commit()

    @staticmethod
    def _git_commit() -> str | None:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True, timeout=2
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return None

    def _path(self, question: Any) -> Path:
        qid = getattr(question, "id_of_question", None)
        if qid is None:
            identity = str(getattr(question, "page_url", None) or getattr(question, "question_text", ""))
            safe_id = "unknown_" + sha256(identity.encode("utf-8")).hexdigest()[:16]
        else:
            safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(qid))
        return self.directory / f"{safe_id}.json"

    def update(self, question: Any, **fields: Any) -> None:
        path = self._path(question)
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
        else:
            record = {
                "schema_version": 1,
                "run_id": self.run_id,
                "run_mode": self.run_mode,
                "code_commit": self.commit,
                "question_id": getattr(question, "id_of_question", None),
                "post_id": getattr(question, "id_of_post", None),
                "question_url": getattr(question, "page_url", None),
                "question_text": getattr(question, "question_text", None),
                "resolution_criteria": getattr(question, "resolution_criteria", None),
                "fine_print": getattr(question, "fine_print", None),
                "close_time": _json_value(getattr(question, "close_time", None)),
                "created_at": _utc_now(),
                "publish_requested": self.publish_requested,
                "submission_status": "not_attempted",
            }
        record.update({key: _json_value(value) for key, value in fields.items()})
        record["updated_at"] = _utc_now()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def mark_report(self, report: Any) -> None:
        question = report.question
        self.update(
            question,
            submission_status="report_returned_publish_requested" if self.publish_requested and not report.errors else (
                "report_returned_with_errors" if report.errors else "dry_run"
            ),
            report_errors=[str(error) for error in report.errors],
            report_completed_at=_utc_now(),
        )
