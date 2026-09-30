import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from scripts.historical_research import run_sample


class HistoricalResearchTests(TestCase):
    @patch("scripts.historical_research.run_research")
    @patch("scripts.historical_research.get_json")
    def test_downloads_questions_and_passes_rules_without_resolution_labels(self, get_json, research):
        get_json.return_value = {"results": [{"id": 1, "question": {"id": 2, "type": "binary", "status": "resolved", "title": "Historical question", "resolution_criteria": "Official criterion", "resolution": "yes", "fine_print": "Exact window"}}]}
        research.return_value = {"assessment": {"probability": 0.7, "verdict": "Yes", "rationale": "Evidence"}, "baseline": {"probability": None}, "probability_shift": None, "searches_used": 3, "pages": [], "error": None}
        with tempfile.TemporaryDirectory() as temp:
            summary = run_sample("token", "tavily", "router", Path(temp))
            report = json.loads((Path(temp) / "1-report.json").read_text())
            self.assertTrue((Path(temp) / "questions.json").exists())
        research.assert_called_once_with("Historical question", "Official criterion", "Exact window", "tavily", "router")
        self.assertFalse(summary["out_of_sample"])
        self.assertFalse(report["out_of_sample"])
        self.assertFalse(summary["submitted_to_metaculus"])

    @patch("scripts.historical_research.run_research")
    @patch("scripts.historical_research.get_json")
    def test_missing_resolution_rules_are_saved_as_failure_without_research(self, get_json, research):
        get_json.return_value = {"results": [{"id": 1, "question": {"id": 2, "type": "binary", "status": "resolved", "title": "Question"}}]}
        with tempfile.TemporaryDirectory() as temp:
            summary = run_sample("token", "tavily", "router", Path(temp))
            self.assertTrue((Path(temp) / "summary.json").exists())
        research.assert_not_called()
        self.assertIn("resolution criteria", summary["results"][0]["error"])
