import json
import io
from unittest.mock import patch
from unittest import TestCase

from ForecastAgent.polymarket_match import parse_candidates, search_candidates


class PolymarketMatchTests(TestCase):
    def market(self, **kwargs):
        return {"id": "one", "question": "Will Anthropic IPO in 2026?", "outcomes": ["Yes", "No"],
                "outcomePrices": [0.6, 0.4], "volumeNum": 100, **kwargs}

    def test_unrelated_year_conflict_does_not_bypass_relevance(self):
        rows = parse_candidates({"markets": [self.market(question="Will Royal win the 2027 French presidential election?")]},
                                "Will Royal Mail raise prices before September 10, 2026?")
        self.assertEqual(rows, [])

    def test_reverse_outcomes_preserve_yes_price_and_token(self):
        rows = parse_candidates({"markets": [self.market(outcomes='["No","Yes"]', outcomePrices='["0.4","0.6"]',
                            clobTokenIds='["no-token","yes-token"]', bestBid=0.39, bestAsk=0.41)]}, "Will Anthropic IPO in 2026?")
        self.assertEqual(rows[0]["yes_probability_display"], 0.6)
        self.assertEqual(rows[0]["yes_token_id"], "yes-token")
        self.assertIsNone(rows[0]["best_bid"])

    def test_malformed_binary_prices_are_not_probabilities(self):
        for values in [dict(outcomes=["Yes", "Other"]), dict(outcomePrices=[0.6, 0.9]),
                       dict(outcomePrices=[True, 0]), dict(outcomePrices='bad'), dict(outcomePrices=[0.6, float('nan')])]:
            with self.subTest(values=values):
                row = parse_candidates({"markets": [self.market(**values)]}, "Will Anthropic IPO in 2026?")[0]
                self.assertIsNone(row["yes_probability_display"])

    def test_event_and_top_level_markets_combined_without_duplicates(self):
        one, two = self.market(), self.market(id="two")
        payload = {"events": [{"markets": [one]}], "markets": [one, two]}
        self.assertEqual(len(parse_candidates(payload, one['question'])), 2)

    def test_closed_and_inactive_contracts_are_excluded(self):
        for flag in [dict(closed=True), dict(resolved=True), dict(active=False)]:
            self.assertEqual(parse_candidates({"markets": [self.market(**flag)]}, "Will Anthropic IPO in 2026?"), [])

    def test_bad_quotes_and_misaligned_tokens_rejected(self):
        row = parse_candidates({"markets": [self.market(bestBid=0.8, bestAsk=0.2, clobTokenIds=['only-one'])]},
                               "Will Anthropic IPO in 2026?")[0]
        self.assertIsNone(row['best_bid'])
        self.assertIsNone(row['best_ask'])
        self.assertIsNone(row['yes_token_id'])

    def test_similar_title_never_authorizes_edge(self):
        row = parse_candidates({"markets": [self.market(description='IPO listing by December 31')]},
                               "Will Anthropic IPO in 2026?")[0]
        self.assertEqual(row['match_score'], 1)
        self.assertFalse(row['eligible_for_edge'])
        self.assertIn('event_stage', row['required_review'])

    @patch("ForecastAgent.polymarket_match.urlopen")
    def test_historical_cutoff_blocks_current_price_lookup(self, urlopen):
        result = search_candidates("Will Anthropic IPO in 2026?", as_of_utc="2026-08-20T00:00:00Z")
        self.assertEqual(result['error'], 'historical_snapshot_required')
        urlopen.assert_not_called()

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

    @patch("ForecastAgent.polymarket_match.urlopen")
    def test_search_records_lookup_time_and_active_filter(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = io.BytesIO(b'{"events": []}')
        result = search_candidates("Will the Fed cut rates in 2026?")
        request = urlopen.call_args.args[0]
        self.assertIn("events_status=active", request.full_url)
        self.assertIn("searched_at", result)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result['raw_response'], {'events': []})
        self.assertEqual(len(result['raw_response_sha256']), 64)
        self.assertFalse(result['historical_safe'])
