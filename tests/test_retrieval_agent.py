import json
import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts.retrieval_agent import RetrievalTask, run_retrieval

REQUEST = {"question": "Will the agency publish the figure?", "resolution_criteria": "Official announcement by the deadline", "mode": "live"}
PLAN = {"needs": [{"id": "status", "condition": "Official publication", "priority": "critical", "expected_source": "Agency", "query": "agency publication"}]}
SEARCH = {"query": "agency status", "need_ids": ["status"], "reason": "Find official current status"}
URL = "https://example.org/announcement"

def call(name, args, ident):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

class RetrievalTests(TestCase):
    def test_historical_current_capture_cannot_finish_as_sufficient(self):
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), {**REQUEST, "mode": "historical_exploratory", "as_of_utc": "2026-08-20T00:00:00Z"})
            task.execute("plan_evidence", PLAN, "")
            task.bundle["evidence"] = [{"id": "E1", "need_ids": ["status"], "evidence_chain": "agency", "temporal_status": "current_capture_possible_later_edits"}]
            result = task.execute("finish_retrieval", {"status": "sufficient", "gaps": [], "conflicts": [], "summary": "Critical condition covered"}, "")
            self.assertEqual(result["status"], "partial")
            self.assertIn("pre-cutoff availability", result["gaps"][0])
    def test_strict_mode_reuses_pre_cutoff_capture_with_hash_validation(self):
        with TemporaryDirectory() as directory:
            raw = b'Official figure was published before the deadline.'
            source = Path(directory) / "source.json"
            page = {"content": raw.decode(), "retrieved_at_utc": "2026-08-01T12:00:00Z",
                    "raw_response_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}
            source.write_text(json.dumps({"pages": {URL: page}}))
            request = {**REQUEST, "mode": "historical_strict", "as_of_utc": "2026-08-20T00:00:00Z", "historical_snapshot_bundle": str(source)}
            task = RetrievalTask(Path(directory) / "task", request)
            task.execute("plan_evidence", PLAN, "")
            with patch("scripts.retrieval_agent.fetch_public_page") as fetch:
                result = task.execute("fetch_page", {"url": URL}, "")
                self.assertEqual(result["temporal_status"], "local_pre_cutoff_capture")
                fetch.assert_not_called()
            self.assertEqual(task.budget()["tavily_basic_remaining"], 3)
            page["sha256"] = "tampered"
            source.write_text(json.dumps({"pages": {URL: page}}))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                RetrievalTask(Path(directory) / "other", request)
    @patch("scripts.retrieval_agent.search_batch", side_effect=RuntimeError("offline"))
    def test_failed_search_budget_survives_process_restart(self, search):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            task = RetrievalTask(path, REQUEST)
            task.execute("plan_evidence", PLAN, "key")
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    task.execute("search_tavily", SEARCH, "key")
                task = RetrievalTask(path, REQUEST)
            with self.assertRaisesRegex(ValueError, "budget exhausted"):
                task.execute("search_tavily", SEARCH, "key")
            self.assertEqual(search.call_count, 3)
            self.assertEqual(task.budget()["tavily_basic_remaining"], 0)
            with self.assertRaisesRegex(ValueError, "different input"):
                RetrievalTask(path, {**REQUEST, "question": "Changed"})

    @patch("scripts.retrieval_agent.search_batch")
    def test_fixed_cutoff_quarantines_unknown_and_future_dates(self, search):
        search.return_value = {"results": [{"url": URL, "published_date": "2026-08-19"},
            {"url": "https://example.org/future", "published_date": "2026-08-21"},
            {"url": "https://example.org/unknown", "published_date": "date unknown"}]}
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), {**REQUEST, "mode": "historical_strict", "as_of_utc": "2026-08-20T00:00:00Z"})
            task.execute("plan_evidence", PLAN, "key")
            result = task.execute("search_tavily", {**SEARCH, "end_date": "2030-01-01"}, "key")
            self.assertEqual(search.call_args.kwargs["end_date"], "2026-08-19")
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(len(task.bundle["quarantine"]), 2)
            with patch("scripts.retrieval_agent.fetch_public_page") as fetch:
                with self.assertRaisesRegex(ValueError, "current web fetch forbidden"):
                    task.execute("fetch_page", {"url": URL}, "key")
                fetch.assert_not_called()

    @patch("scripts.retrieval_agent.fetch_public_page")
    @patch("scripts.retrieval_agent.search_batch")
    @patch("scripts.retrieval_agent.ask_ultra")
    def test_full_collection_exact_quotes_coverage_and_zero_network_replay(self, ask, search, fetch):
        search.return_value = {"results": [{"url": URL, "published_date": "2026-09-29"}]}
        text = "The official agency published the figure on September 29. " * 3
        fetch.return_value = {"content": text, "url": URL, "retrieved_at_utc": "2026-09-30T00:00:00Z", "sha256": "abc"}
        evidence = {"url": URL, "claim": "Agency published", "quote": "The official agency published the figure on September 29.", "need_ids": ["status"],
            "stance": "supports", "original_source": "Agency", "event_time": "2026-09-29",
            "quality": {k: "Direct statement" for k in ["authority", "directness", "relevance", "verifiability"]}}
        finish = {"status": "sufficient", "gaps": [], "conflicts": [], "summary": "Official publication verified"}
        ask.side_effect = [{"tool_calls": [call(n, a, str(i))]} for i, (n, a) in enumerate([
            ("plan_evidence", PLAN), ("search_tavily", SEARCH), ("fetch_page", {"url": URL}),
            ("record_evidence", {**evidence, "quote": "A fabricated quote does not occur here"}),
            ("finish_retrieval", finish), ("record_evidence", evidence), ("finish_retrieval", finish)])]
        with TemporaryDirectory() as directory:
            bundle = run_retrieval(REQUEST, directory, "tavily", "router")
            self.assertEqual(bundle["result"]["status"], "sufficient")
            self.assertEqual(len(bundle["evidence"]), 1)
            self.assertIn("Supporting quote", bundle["transcript"][3]["result"]["error"])
            self.assertIn("critical coverage", bundle["transcript"][4]["result"]["error"])
            counts = (ask.call_count, search.call_count, fetch.call_count)
            self.assertEqual(run_retrieval({}, directory, "", "", replay=True), bundle)
            self.assertEqual(run_retrieval(REQUEST, directory, "", ""), bundle)
            self.assertEqual(counts, (ask.call_count, search.call_count, fetch.call_count))
            self.assertNotIn("probability", bundle["result"])

    @patch("scripts.retrieval_agent.search_batch", return_value={"results": []})
    @patch("scripts.retrieval_agent.ask_ultra")
    def test_four_searches_in_one_model_message_cannot_exceed_budget(self, ask, search):
        ask.side_effect = [{"tool_calls": [call("plan_evidence", PLAN, "p")]},
            {"tool_calls": [call("search_tavily", SEARCH, str(n)) for n in range(4)]},
            {"tool_calls": [call("finish_retrieval", {"status": "failed", "gaps": ["No evidence"], "conflicts": [], "summary": "Unavailable"}, "f")]}]
        with TemporaryDirectory() as directory:
            bundle = run_retrieval(REQUEST, directory, "key", "router")
            self.assertEqual(search.call_count, 3)
            self.assertEqual(bundle["resources"]["tavily_basic_attempts"], 3)
            self.assertIn("budget exhausted", bundle["transcript"][4]["result"]["error"])

    def test_concurrent_run_fails_before_network(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / ".running.lock").touch()
            with patch("scripts.retrieval_agent.ask_ultra") as ask:
                with self.assertRaisesRegex(RuntimeError, "concurrent"):
                    run_retrieval(REQUEST, directory, "key", "router")
                ask.assert_not_called()
