import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from ForecastAgent.forecast_snapshots import SnapshotStore


class SnapshotStoreTests(TestCase):
    def test_preserves_research_and_prediction_in_one_question_record(self) -> None:
        question = SimpleNamespace(
            id_of_question=123,
            id_of_post=456,
            page_url="https://www.metaculus.com/questions/456/",
            question_text="Will it happen?",
            resolution_criteria="Yes if it happens.",
            fine_print="",
            close_time=None,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory, run_mode="test_questions", publish_requested=False)
            store.update(question, research_text="source at 2026-09-29")
            store.update(question, model_probability=0.63, model_reasoning="reason")
            store.mark_report(SimpleNamespace(question=question, errors=[]))
            path = Path(directory) / store.run_id / "123.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["research_text"], "source at 2026-09-29")
            self.assertEqual(record["model_probability"], 0.63)
            self.assertEqual(record["submission_status"], "dry_run")
            self.assertEqual(record["question_id"], 123)
