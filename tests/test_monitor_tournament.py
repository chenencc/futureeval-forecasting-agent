import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from scripts.monitor_tournament import collect_questions, snapshot_questions


class MonitorTournamentTests(TestCase):
    @patch("scripts.monitor_tournament.get_json")
    def test_saves_question_snapshot_from_one_request(self, get_json) -> None:
        post = {"id": 123, "title": "Will it happen?", "status": "open", "question": {"id": 456, "resolution_criteria": "Official announcement"}}
        get_json.return_value = {"results": [post], "next": None}
        with tempfile.TemporaryDirectory() as directory:
            output = snapshot_questions("test-token", Path(directory))
            index = json.loads((output / "index.json").read_text(encoding="utf-8"))
            saved = json.loads((output / "123.json").read_text(encoding="utf-8"))

        self.assertEqual(index["question_count"], 1)
        self.assertEqual(index["questions"][0]["question_id"], 456)
        self.assertEqual(saved["post"]["question"]["resolution_criteria"], "Official announcement")
        self.assertEqual(get_json.call_count, 1)

    @patch("scripts.monitor_tournament.get_json")
    def test_empty_tournament_is_valid(self, get_json) -> None:
        get_json.return_value = {"results": [], "next": None}
        self.assertEqual(collect_questions("test-token"), [])
