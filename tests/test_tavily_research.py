import io
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from tavily_research import search_question


class TavilyResearchTests(TestCase):
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
        self.assertFalse(payload["include_answer"])
        self.assertEqual(report.count("https://example.org/update"), 1)
        self.assertIn("2026-09-29", report)
        self.assertIn("Latest data with details", report)
        self.assertNotIn("test-secret", report)
