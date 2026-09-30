import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.monitor_tournament import API_ROOT, _initial_url, collect_archive_pages, collect_pages, snapshot_questions


def post(post_id: int, question_id: int, status: str = "open") -> dict:
    return {"id": post_id, "title": f"Question {question_id}", "status": status,
            "question": {"id": question_id, "title": f"Question {question_id}",
                         "status": status, "type": "binary", "resolution_criteria": "Official report"}}


class MonitorTournamentTests(TestCase):
    @patch("ForecastAgent.monitor_tournament.get_json")
    def test_follows_next_even_when_first_page_is_short(self, get_json) -> None:
        page_two = API_ROOT + "?offset=5"
        get_json.side_effect = [
            {"results": [post(1, 11)], "next": page_two},
            {"results": [post(2, 22)], "next": None},
        ]
        rows, next_url, pages = collect_pages("token", _initial_url(True), 30)
        self.assertEqual([row["id"] for row in rows], [1, 2])
        self.assertIsNone(next_url)
        self.assertEqual(pages, 2)

    @patch("ForecastAgent.monitor_tournament.get_json")
    def test_new_ids_are_persisted_and_closed_questions_are_archived(self, get_json) -> None:
        get_json.side_effect = [
            {"results": [post(1, 11)], "next": None},
            {"results": [post(1, 11), post(2, 22, "closed"), {"id": 3, "title": "Announcement"}], "next": None},
            {"results": [post(1, 11)], "next": None},
            {"results": [post(1, 11), post(2, 22, "closed")], "next": None},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = snapshot_questions("token", root)
            first_index = json.loads((first / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(first_index["new_question_count"], 2)
            self.assertEqual(first_index["question_count"], 2)
            self.assertTrue((first / "2.json").exists())
            self.assertFalse((first / "3.json").exists())
            second = snapshot_questions("token", root)
            second_index = json.loads((second / "index.json").read_text(encoding="utf-8"))
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(second_index["new_question_count"], 0)
        self.assertEqual(state["seen_question_ids"], [11, 22])

    @patch("ForecastAgent.monitor_tournament.get_json")
    def test_archive_cursor_resumes_next_run(self, get_json) -> None:
        urls = []
        pages = [API_ROOT + f"?offset={n}" for n in range(1, 6)]

        def fake_get(url, token):
            urls.append(url)
            if "statuses=open" in url:
                return {"results": [], "next": None}
            offset = len([item for item in urls if "statuses=open" not in item]) - 1
            return {"results": [post(100 + offset, 200 + offset, "closed")],
                    "next": pages[offset] if offset < len(pages) else None}

        get_json.side_effect = fake_get
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_questions("token", root)
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["archive_next_url"], pages[4])
            snapshot_questions("token", root)
        self.assertEqual(urls[7], pages[4])

    @patch("ForecastAgent.monitor_tournament.get_json")
    def test_archive_keeps_completed_pages_after_a_failed_request(self, get_json) -> None:
        page_two = API_ROOT + "?offset=5"
        get_json.side_effect = [
            {"results": [post(1, 11, "closed")], "next": page_two},
            RuntimeError("rate limit"),
        ]
        rows, cursor, pages, error = collect_archive_pages("token", _initial_url(False))
        self.assertEqual([row["id"] for row in rows], [1])
        self.assertEqual(cursor, page_two)
        self.assertEqual(pages, 1)
        self.assertIn("rate limit", error)

    @patch("ForecastAgent.retrieval_agent.run_retrieval")
    @patch("ForecastAgent.monitor_tournament.get_json")
    def test_researches_open_binary_once_without_submission(self, get_json, run_research) -> None:
        get_json.return_value = {"results": [post(1, 11)], "next": None}
        run_research.return_value = {"result": {"status": "partial"}}
        with patch.dict("os.environ", {"TAVILY_API_KEY": "test", "OPENROUTER_API_KEY": "test"}):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                snapshot_questions("token", root, research=True)
                snapshot_questions("token", root, research=True)
        self.assertEqual(run_research.call_count, 1)
