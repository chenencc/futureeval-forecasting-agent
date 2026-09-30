import json
import io
from unittest.mock import patch
from unittest import TestCase

from polymarket_match import parse_candidates, search_candidates


class PolymarketMatchTests(TestCase):
    def test_prices_child_contract_not_parent_event(self) -> None:
        payload = {"events": [{
            "title": "How many Fed cuts in 2026?",
            "slug": "fed-cuts-2026",
            "description": "Resolve using the FOMC announcement.",
            "markets": [
                {"id": "1", "question": "Will the Fed make zero cuts in 2026?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.89","0.11"]', "volumeNum": 100},
                {"id": "2", "question": "Will the Fed make one cut in 2026?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.25","0.75"]', "volumeNum": 100},
            ],
        }]}
        rows = parse_candidates(payload, "Will the Fed make one cut in 2026?")
        self.assertEqual(rows[0]["market_id"], "2")
        self.assertEqual(rows[0]["yes_probability_display"], 0.25)
        self.assertEqual(rows[0]["event_title"], "How many Fed cuts in 2026?")
        self.assertEqual(rows[0]["resolution_rules"], "Resolve using the FOMC announcement.")
        self.assertEqual(len(rows), 2)

    def test_rejects_untouched_price_and_flags_different_year(self) -> None:
        payload = {"markets": [
            {"id": "1", "question": "Will the Fed make one cut in 2026?", "outcomes": ["Yes", "No"], "outcomePrices": [0.5, 0.5], "volumeNum": 0},
            {"id": "2", "question": "Will the Fed make one cut in 2027?", "outcomes": ["Yes", "No"], "outcomePrices": [0.6, 0.4], "volumeNum": 100},
        ]}
        rows = parse_candidates(payload, "Will the Fed make one cut in 2026?")
        self.assertEqual(rows[0]["price_status"], "unquoted_or_untraded")
        self.assertIsNone(rows[0]["yes_probability_display"])
        self.assertEqual(rows[1]["match_status"], "year_conflict")

    @patch("polymarket_match.urlopen")
    def test_search_records_lookup_time_and_active_filter(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = io.BytesIO(b'{"events": []}')
        result = search_candidates("Will the Fed cut rates in 2026?")
        request = urlopen.call_args.args[0]
        self.assertIn("events_status=active", request.full_url)
        self.assertIn("searched_at", result)
        self.assertEqual(result["candidates"], [])
