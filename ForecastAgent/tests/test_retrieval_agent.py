import json
import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.retrieval_agent import RetrievalTask, run_retrieval

REQUEST = {"question": "Will the agency publish the figure?", "resolution_criteria": "Official announcement by the deadline", "mode": "live"}
PLAN = {"needs": [{"id": "status", "condition": "Official publication", "priority": "critical", "expected_source": "Agency", "query": "agency publication"}]}
SEARCH = {"query": "agency status", "need_ids": ["status"], "reason": "Find official current status"}
URL = "https://example.org/announcement"

def call(name, args, ident):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

class RetrievalTests(TestCase):
    @patch("ForecastAgent.retrieval_agent.search_batch", return_value={"results": []})
    def test_model_search_choices_are_durable_with_same_three_attempt_cap(self, search):
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), REQUEST)
            task.execute("plan_evidence", PLAN, "key")
            choices = {**SEARCH, "query": '"Agency" announcement', "topic": "news", "include_domains": ["agency.gov"], "include_domains_mode": "prefer", "exact_match": True}
            task.execute("search_tavily", choices, "key")
            self.assertEqual(search.call_args.kwargs["topic"], "news")
            self.assertTrue(search.call_args.kwargs["exact_match"])
            restored = RetrievalTask(Path(directory), REQUEST)
            self.assertEqual(restored.bundle["searches"][0]["search_options"]["include_domains"], ["agency.gov"])
            for _ in range(2):
                restored.execute("search_tavily", choices, "key")
            with self.assertRaisesRegex(ValueError, "budget exhausted"):
                restored.execute("search_tavily", choices, "key")
            self.assertEqual(search.call_count, 3)

    @patch("ForecastAgent.retrieval_agent.extract_basic")
    def test_extract_rescue_eligibility_partial_response_and_restart_budget(self, extract):
        other = "https://example.org/other"
        extract.return_value = {"results": [{"url": URL, "raw_content": "Official announcement establishes the current status. " * 3},
                                              {"url": "https://unrequested.example/", "raw_content": "Unrequested text " * 20}],
                                "failed_results": [{"url": other, "error": "unavailable"}], "usage": {"credits": 0}}
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), REQUEST)
            task.execute("plan_evidence", PLAN, "key")
            task.bundle["searches"] = [{"results": [{"url": URL}, {"url": other}]}]
            args = {"urls": [URL, other], "need_ids": ["status"], "reason": "Official sources for critical status"}
            with self.assertRaisesRegex(ValueError, "free-fetch-failed"):
                task.execute("extract_failed_pages", args, "key")
            extract.assert_not_called()
            task.bundle["fetch_attempts"] = [{"url": u, "status": "failed"} for u in args["urls"]]
            result = task.execute("extract_failed_pages", args, "key")
            self.assertEqual(len(result["pages"]), 1)
            self.assertEqual(len(result["failed_results"]), 1)
            self.assertEqual(len(task.bundle["pages"]), 1)
            self.assertEqual(task.bundle["pages"][URL]["capture_method"], "tavily_basic_extract")
            restored = RetrievalTask(Path(directory), REQUEST)
            with self.assertRaisesRegex(ValueError, "batch budget exhausted"):
                restored.execute("extract_failed_pages", args, "key")
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(restored.budget()["basic_extract_batches_remaining"], 0)

    @patch("ForecastAgent.retrieval_agent.extract_basic", side_effect=RuntimeError("offline"))
    def test_extract_failure_consumes_budget_and_strict_blocks_network(self, extract):
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), REQUEST)
            task.execute("plan_evidence", PLAN, "key")
            task.bundle["searches"] = [{"results": [{"url": URL}]}]
            task.bundle["fetch_attempts"] = [{"url": URL, "status": "failed"}]
            args = {"urls": [URL], "need_ids": ["status"], "reason": "Critical status"}
            with self.assertRaisesRegex(RuntimeError, "budget consumed"):
                task.execute("extract_failed_pages", args, "key")
            restored = RetrievalTask(Path(directory), REQUEST)
            with self.assertRaises(ValueError):
                restored.execute("extract_failed_pages", args, "key")
            strict = RetrievalTask(Path(directory) / "strict", {**REQUEST, "mode": "historical_strict", "as_of_utc": "2026-08-20T00:00:00Z"})
            strict.execute("plan_evidence", PLAN, "key")
            with self.assertRaisesRegex(ValueError, "strict forbids"):
                strict.execute("extract_failed_pages", args, "key")
            self.assertEqual(extract.call_count, 1)

    def test_historical_current_capture_cannot_finish_as_sufficient(self):
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), {**REQUEST, "mode": "historical_exploratory", "as_of_utc": "2026-08-20T00:00:00Z"})
            task.execute("plan_evidence", PLAN, "")
            task.bundle["evidence"] = [{"id": "E1", "claim": "Agency publication", "need_ids": ["status"], "evidence_chain": "agency", "temporal_status": "current_capture_possible_later_edits", "audit": {"accepted": True}}]
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
            with patch("ForecastAgent.retrieval_agent.fetch_public_page") as fetch:
                result = task.execute("fetch_page", {"url": URL}, "")
                self.assertEqual(result["temporal_status"], "local_pre_cutoff_capture")
                fetch.assert_not_called()
            self.assertEqual(task.budget()["tavily_basic_remaining"], 3)
            page["sha256"] = "tampered"
            source.write_text(json.dumps({"pages": {URL: page}}))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                RetrievalTask(Path(directory) / "other", request)
    @patch("ForecastAgent.retrieval_agent.search_batch", side_effect=RuntimeError("offline"))
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

    @patch("ForecastAgent.retrieval_agent.search_batch")
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
            with patch("ForecastAgent.retrieval_agent.fetch_public_page") as fetch:
                with self.assertRaisesRegex(ValueError, "current web fetch forbidden"):
                    task.execute("fetch_page", {"url": URL}, "key")
                fetch.assert_not_called()

    @patch("ForecastAgent.retrieval_agent.fetch_public_page")
    @patch("ForecastAgent.retrieval_agent.search_batch")
    @patch("ForecastAgent.retrieval_agent.ask_ultra")
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
            ("finish_retrieval", finish), ("record_evidence", evidence),
            ("audit_evidence", {"reviews": [{"evidence_id": "E1", "entity_matches": True, "quote_supports_claim": True, "time_valid": True, "reason": "Direct agency publication"}]}), ("finish_retrieval", finish)])]
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

    @patch("ForecastAgent.retrieval_agent.search_batch", return_value={"results": []})
    @patch("ForecastAgent.retrieval_agent.ask_ultra")
    def test_four_searches_in_one_model_message_cannot_exceed_budget(self, ask, search):
        ask.side_effect = [{"tool_calls": [call("plan_evidence", PLAN, "p")]},
            {"tool_calls": [call("search_tavily", {**SEARCH, "query": f"agency status {n}"}, str(n)) for n in range(4)]},
            {"tool_calls": [call("finish_retrieval", {"status": "failed", "gaps": ["No evidence"], "conflicts": [], "summary": "Unavailable"}, "f")]}]
        with TemporaryDirectory() as directory:
            bundle = run_retrieval(REQUEST, directory, "key", "router")
            self.assertEqual(search.call_count, 3)
            self.assertEqual(bundle["resources"]["tavily_basic_attempts"], 3)
            self.assertIn("budget exhausted", bundle["transcript"][4]["result"]["error"])

    def test_concurrent_run_fails_before_network(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / ".running.lock").touch()
            with patch("ForecastAgent.retrieval_agent.ask_ultra") as ask:
                with self.assertRaisesRegex(RuntimeError, "concurrent"):
                    run_retrieval(REQUEST, directory, "key", "router")
                ask.assert_not_called()
