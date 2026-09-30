import json
from unittest import TestCase
from unittest.mock import patch

from scripts.ultra_research_agent import run_research, validate_assessment


def tool_call(name: str, arguments: dict, ident: str) -> dict:
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class UltraResearchAgentTests(TestCase):
    @patch("scripts.ultra_research_agent.fetch_public_page")
    @patch("scripts.ultra_research_agent.search_batch")
    @patch("scripts.ultra_research_agent.ask_ultra")
    def test_ultra_selects_search_and_fetch_before_read_only_assessment(self, ask, search, fetch) -> None:
        url = "https://example.org/data"
        search.return_value = {"query": "event data", "searched_at": "2026-09-30T00:00:00Z", "results": [{"url": url, "title": "Data"}]}
        fetch.return_value = {"url": url, "final_url": url, "retrieved_at_utc": "2026-09-30T00:00:01Z", "sha256": "abc", "content": "Official figure: 12", "content_truncated": False}
        assessment = {
            "probability": 0.6, "verdict": "Yes", "base_rate": "No comparable reference class",
            "event_paths": ["Yes if the total remains above the threshold"],
            "evidence": [{"url": url, "claim": "The figure is 12", "source_type": "primary", "quality": "high", "reason": "Direct official data"}],
            "contradictions": [], "remaining_unknowns": [], "rationale": "The observed figure is above the threshold.",
        }
        ask.side_effect = [
            {"tool_calls": [tool_call("search_tavily", {"query": "event data"}, "1")]},
            {"tool_calls": [tool_call("fetch_page", {"url": url}, "2")]},
            {"tool_calls": [tool_call("finish_research", assessment, "3")]},
        ]
        report = run_research("Will it happen?", "Official figure above 10", "", "tavily", "router")
        self.assertFalse(report["submitted_to_metaculus"])
        self.assertEqual(report["searches_used"], 1)
        self.assertEqual(report["assessment"]["probability"], 0.6)
        self.assertEqual(report["pages"][0]["content"], "Official figure: 12")
        self.assertIsNone(report["error"])

    @patch("scripts.ultra_research_agent.search_batch")
    @patch("scripts.ultra_research_agent.ask_ultra")
    def test_fourth_search_is_rejected_by_code(self, ask, search) -> None:
        search.return_value = {"query": "query", "searched_at": "now", "results": []}
        ask.side_effect = [
            {"tool_calls": [tool_call("search_tavily", {"query": str(n)}, str(n))]}
            for n in range(4)
        ] + [{"content": "stopped"}]
        report = run_research("Will it happen?", "", "", "tavily", "router")
        self.assertEqual(report["searches_used"], 3)
        self.assertEqual(search.call_count, 3)
        self.assertIn("Three-search budget exhausted", report["tool_transcript"][3]["result"]["error"])

    def test_cannot_cite_an_unfetched_search_hit(self) -> None:
        with self.assertRaisesRegex(ValueError, "fetched page"):
            validate_assessment({
                "probability": 0.7, "verdict": "Yes", "base_rate": "", "event_paths": [],
                "evidence": [{"url": "https://example.org/only-searched", "claim": "x", "source_type": "primary", "quality": "high", "reason": "x"}],
                "contradictions": [], "remaining_unknowns": [], "rationale": "x",
            }, {})
