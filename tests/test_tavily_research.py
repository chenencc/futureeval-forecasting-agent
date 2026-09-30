import io
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from tavily_research import followup_query_from_response, search_batch, search_question


class TavilyResearchTests(TestCase):
    @patch("tavily_research.urlopen")
    def test_historical_date_is_sent_with_basic_and_no_answer(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.BytesIO(b'{"results":[]}')
        search_batch("historical status", "key", end_date="2026-08-19")
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["end_date"], "2026-08-19")
        self.assertEqual(payload["search_depth"], "basic")
        self.assertFalse(payload["include_answer"])
        self.assertEqual(urlopen.call_count, 1)
    def test_followup_query_parses_ultra_response_and_falls_back(self) -> None:
        self.assertEqual(
            followup_query_from_response("Reasoning\nQUERY:  agency  2026  update", "Will it happen?"),
            "agency 2026 update",
        )
        self.assertIn(
            "latest official status",
            followup_query_from_response("No parseable query", "Will it happen?"),
        )

    @patch("tavily_research.urlopen")
    def test_uses_one_basic_search_and_returns_cited_snippets(self, urlopen: Mock) -> None:
        body = {
            "results": [
                {
                    "title": "Agency update",
                    "url": "https://example.org/update",
                    "published_date": "2026-09-29",
                    "content": "Latest data\nwith details",
                },
                {
                    "title": "Duplicate",
                    "url": "https://example.org/update",
                    "content": "Duplicate data",
                },
            ]
        }
        urlopen.return_value.__enter__.return_value = io.BytesIO(
            json.dumps(body).encode("utf-8")
        )

        report = search_question("Will  the  event happen?", "test-secret")

        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        payload = json.loads(request.data)
        self.assertEqual(payload["search_depth"], "basic")
        self.assertEqual(payload["max_results"], 10)
        self.assertFalse(payload["include_answer"])
        self.assertEqual(payload["exclude_domains"], ["metaculus.com"])
        self.assertEqual(report.count("https://example.org/update"), 1)
        self.assertIn("2026-09-29", report)
        self.assertIn("Latest data with details", report)
        self.assertNotIn("test-secret", report)

    @patch("tavily_research.urlopen")
    def test_followup_keeps_only_new_urls_up_to_ten(self, urlopen: Mock) -> None:
        body = {"results": [
            {"url": "https://example.org/old/?utm_source=search", "title": "Old"},
            {"url": "https://example.org/new#section", "title": "New"},
            {"url": "https://example.org/new/", "title": "Duplicate new"},
            *({"url": f"https://example.org/item/{n}", "title": str(n)} for n in range(12)),
        ]}
        urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps(body).encode())

        batch = search_batch("Targeted follow-up", "test-secret", exclude_urls=("https://example.org/old",))

        self.assertEqual(len(batch["results"]), 10)
        self.assertEqual(batch["results"][0]["title"], "New")
        self.assertFalse(any(hit["title"] in {"Old", "Duplicate new"} for hit in batch["results"]))
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)["max_results"], 10)
