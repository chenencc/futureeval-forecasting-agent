"""Missing and illiquid books must not lose eligible research questions."""
import copy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.common import digest, instant, now, policy, read, write
from ForecastAgent.market_repricing.monitor import enroll, ingest
from ForecastAgent.market_repricing.observations import admission_quote, forecast_context
from ForecastAgent.market_repricing.tests.test_repricing import CLOCK, raw_poly
from ForecastAgent.market_repricing.venues import normalize, screen, quote


class ObservationTests(unittest.TestCase):
    def test_admits_independent_of_quote_availability_size_and_spread(self):
        for observed in (None, {'status': 'fetch_failed'}, {'status': 'missing_two_sided_book'},
                         {'status': 'shallow_book'}, {'status': 'wide_spread'}, {'status': 'crossed_book'}):
            with self.subTest(observed=observed):
                ledger = {'markets': {}, 'tasks': {}, 'identity': {'policy_sha256': digest(policy())}}
                ingest(ledger, 'polymarket', [raw_poly()], policy(), CLOCK)
                market = next(iter(ledger['markets'].values()))
                if observed:
                    market['last_quote'] = observed | {'received_at': CLOCK, 'midpoint': None}
                enroll(ledger, policy(), CLOCK)
                self.assertEqual(len(ledger['tasks']), 1)
                task = next(iter(ledger['tasks'].values()))
                self.assertFalse(task['admission_quote']['research_admission_depends_on_book'])
                before = copy.deepcopy(task)
                enroll(ledger, policy(), CLOCK)
                self.assertEqual(next(iter(ledger['tasks'].values())), before)

    def test_actual_wide_shallow_price_retains_its_quality_and_original(self):
        raw = {'bids': [{'price': .03, 'size': 18.5}], 'asks': [{'price': .95, 'size': 8.26}]}
        observed = quote('polymarket', raw, policy(), {'received_at': CLOCK})
        before = copy.deepcopy(observed)
        context = forecast_context(observed, CLOCK)
        self.assertEqual(observed, before)
        self.assertEqual(context['status'], 'shallow_book')
        self.assertEqual(context['original_quote'], observed)
        self.assertAlmostEqual(context['midpoint'], .49)
        self.assertIn('wide_spread', context['quality_flags'])
        self.assertIn('shallow_book', context['quality_flags'])
        self.assertFalse(context['midpoint_is_executable_price'])

    def test_invalid_metadata_does_not_become_a_quote_only_gap(self):
        ledger = {'markets': {}, 'tasks': {}, 'identity': {'policy_sha256': digest(policy())}}
        ingest(ledger, 'polymarket', [raw_poly()], policy(), CLOCK)
        next(iter(ledger['markets'].values()))['admission_metadata_error'] = {'reason': 'wrong_market_identity'}
        enroll(ledger, policy(), CLOCK)
        self.assertFalse(ledger['tasks'])

    def test_missing_crossed_zero_depth_and_stale_cannot_invent_a_price(self):
        examples = [dict(status='missing_two_sided_book'), dict(status='fetch_failed'),
            dict(status='crossed_book', yes_bid=.9, yes_ask=.2, yes_bid_size=30, yes_ask_size=30),
            dict(status='shallow_book', yes_bid=.2, yes_ask=.8, yes_bid_size=0, yes_ask_size=20),
            dict(status='usable', midpoint=.5, received_at='2026-10-11T23:58:00Z')]
        for raw in examples:
            raw = {'received_at': CLOCK, **raw}
            context = forecast_context(raw, CLOCK)
            self.assertIsNone(context['midpoint'])
            self.assertFalse(context['baseline_available'])
            self.assertEqual(context['original_quote'], raw)
            self.assertEqual(context['quote_observed_at'], raw['received_at'])
            self.assertEqual(context['received_at_role'], 'analysis_context_frozen_at')

    def test_missing_quote_has_attempt_clock_without_fake_observation(self):
        context = forecast_context({'status': 'fetch_failed', 'attempted_at': CLOCK}, CLOCK)
        self.assertIsNone(context['quote_observed_at'])
        self.assertIsNone(context['midpoint'])

    def test_business_measurements_are_research_events_without_intake_model(self):
        for question in ('Will Walmart Foot Traffic for October 2026 be above 116?',
                         'Will Costco Credit Card Spend for October 2026 be above 110?'):
            market = normalize('polymarket', raw_poly() | {'question': question})
            self.assertEqual(screen(market, CLOCK, policy())['status'], 'candidate')

    def test_sports_entities_in_real_business_and_legal_events_are_not_wagers(self):
        for question in ('Will the NFL face an antitrust lawsuit?', 'Will NBA revenue exceed its estimate?'):
            market = normalize('polymarket', raw_poly() | {'question': question})
            self.assertEqual(screen(market, CLOCK, policy())['status'], 'candidate')

    def test_gambling_remains_outside_research_and_time_limits_still_apply(self):
        market = normalize('polymarket', raw_poly())
        self.assertEqual(screen(market | {'screen_text': 'NFL exact score'}, CLOCK, policy())['status'], 'excluded')
        self.assertEqual(screen(market, (instant(CLOCK)+timedelta(hours=3)).isoformat(), policy())['status'], 'outside_new_question_window')

    def test_worker_seals_model_result_without_requiring_live_book(self):
        from ForecastAgent.market_repricing.worker import execute
        for book_result in ({'status': 'missing_two_sided_book', 'midpoint': None}, TimeoutError('book unavailable')):
            with self.subTest(book_result=book_result), tempfile.TemporaryDirectory() as directory:
                market = normalize('polymarket', raw_poly())
                ledger = {'tasks': {market['key']: {'market': market, 'status': 'queued'}}}
                bundle = {'request': {}, 'pages': {'source': {'content': 'Real original text'}}}
                seen = []
                def score(bundle, market, anchor, root, cfg):
                    seen.append(anchor)
                    return {'status': 'completed', 'arms': {'original': {'status': 'completed', 'eventual_probability_yes': .3}},
                            'baseline': {'24': anchor['midpoint']}}
                with patch('ForecastAgent.market_repricing.worker.refresh', return_value=(market, {})), patch(
                        'ForecastAgent.market_repricing.worker.screen', return_value={'status': 'candidate'}), patch(
                        'ForecastAgent.market_repricing.worker.book', side_effect=book_result if isinstance(book_result, Exception) else None,
                        return_value=book_result | {'received_at': now()} if isinstance(book_result, dict) else None), patch(
                        'ForecastAgent.market_repricing.materials.prepare', return_value=(bundle, {})):
                    execute(directory, ledger, market['key'], policy(), None, collector=lambda *a: bundle, forecaster=score)
                self.assertEqual(ledger['tasks'][market['key']]['status'], 'completed')
                self.assertEqual(ledger['tasks'][market['key']]['executions'], 1)
                self.assertIsNone(seen[0]['midpoint'])
                self.assertTrue(read(Path(directory)/'tasks'/digest(market['key'])/'anchor.json')['original_quote'])


if __name__ == '__main__':
    unittest.main()
