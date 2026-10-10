"""Predictive background retention, review isolation and provider failure records."""
import copy
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from ForecastAgent.analysis.pilot import load
from ForecastAgent.research_loop import decision_http as decisions
from ForecastAgent.research_loop import decision, forecast_map, live_trial
from ForecastAgent.research_loop.acceptance import accept, MapAcceptanceError
from ForecastAgent.tests.test_research_live_trial import message
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_simple_map import literal_proposal


class ForecastMapTests(unittest.TestCase):
    def test_unbound_date_isolated_without_discarding_literal_background(self):
        b = source_bundle(); p = literal_proposal(b); p['nodes'] = p['nodes'][:1]
        p['material_requests'] = []; p['nodes'][0].update(event_time='invented release date', time_status='source_stated')
        before = copy.deepcopy(p)
        audit = accept(b, p, map_protocol=forecast_map.PROTOCOL)['acceptance']
        self.assertEqual(p, before)
        self.assertEqual(audit['accepted_node_ids'], ['observed_report'])
        self.assertEqual(audit['rejected'][0]['section'], 'node_annotations')
        kept = b['research_loop']['current']['nodes'][0]
        self.assertEqual(kept['claim'], before['nodes'][0]['claim'])
        self.assertTrue(kept['literal_quote_verified'])
        self.assertEqual(kept['time_status'], 'unknown'); self.assertEqual(kept['event_time'], '')

    def test_date_isolation_never_rescues_an_invented_quote_or_stale_handle(self):
        for change in ({'claim': 'Revenue will be 24 billion dollars.'}, {'evidence_ids': ['Rinvented']}):
            b = source_bundle(); p = literal_proposal(b); p['nodes'] = p['nodes'][:1]
            p['material_requests'] = []; p['nodes'][0].update(event_time='invented date', time_status='source_stated', **change)
            with self.assertRaises(MapAcceptanceError):
                accept(b, p, map_protocol=forecast_map.PROTOCOL)

    def test_exact_body_aliases_grouped_without_removing_different_versions(self):
        view = {'sources': [{'source_id': 'a', 'body_sha256': 'hash1'},
            {'source_id': 'b', 'body_sha256': 'hash1'}, {'source_id': 'c', 'body_sha256': 'hash2'}],
            'evidence': [{'source_id': 'a', 'evidence_id': 'Ra', 'text': 'full original'},
                {'source_id': 'b', 'evidence_id': 'Rb', 'text': 'full original'},
                {'source_id': 'c', 'evidence_id': 'Rc', 'text': 'revision'}]}
        before = copy.deepcopy(view); groups = forecast_map.inventory(view)
        self.assertEqual(view, before)
        same = next(g for g in groups if g['same_body_aliases'])
        self.assertEqual(same['source_ids'], ['a', 'b'])
        self.assertEqual(same['evidence_ids'], ['Ra', 'Rb'])
        self.assertEqual(len(groups), 2)

    def test_bad_review_does_not_discard_a_valid_background_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = source_bundle(); before = copy.deepcopy(bundle)
            baseline = decision.prepare(bundle)['baseline']; proposal = literal_proposal(bundle)
            proposal['source_reviews'] = [{'source_group_id': 'invented', 'role': 'baseline',
                'node_ids': ['observed_report'], 'reason': 'Earlier report, final target unknown.'}]
            with patch.object(live_trial, 'ask_model', return_value=message(proposal)) as model:
                mapped = live_trial.map_stage(bundle, baseline, tmp, 'offline', map_protocol=forecast_map.PROTOCOL)
            self.assertEqual(model.call_count, 1)
            review = load(Path(tmp)/'coverage-review.json')
            self.assertTrue(review['rejected']); self.assertFalse(review['acceptance_gate'])
            prepared = decision.prepare(mapped)
            self.assertEqual(prepared['baseline'], baseline)
            self.assertTrue(all(prepared['enriched'][k] == v for k, v in baseline.items()))
            self.assertEqual(prepared['enriched']['research_map']['nodes'][0]['applicability'], 'background')
            self.assertEqual(prepared['enriched']['research_map']['map_protocol'], forecast_map.PROTOCOL)
            self.assertNotIn('source_reviews', prepared['enriched']['research_map'])
            self.assertEqual(mapped['pages'], before['pages'])

    def test_cross_source_or_quarantined_node_cannot_pass_coordinate_review(self):
        groups = [{'source_group_id': 'S1', 'evidence_ids': ['R1']},
                  {'source_group_id': 'S2', 'evidence_ids': ['R2']}]
        nodes = [{'id': 'one', 'kind': 'observation', 'evidence_ids': ['R1']}]
        reviews = [{'source_group_id': 'S2', 'role': 'target', 'node_ids': ['one'], 'reason': 'wrong source'},
                   {'source_group_id': 'S1', 'role': 'baseline', 'node_ids': ['bad'], 'reason': 'quarantined node'}]
        audit = forecast_map.review_audit(reviews, groups, nodes)
        self.assertEqual(len(audit['rejected']), 2)
        self.assertEqual(audit['unreviewed_observation_ids'], ['one'])
        self.assertFalse(audit['meaning_verified'])

    def test_future_gap_can_coexist_with_baseline_and_reviews_are_not_truth(self):
        groups = [{'source_group_id': 'S1', 'evidence_ids': ['R1']}]
        nodes = [{'id': 'prior', 'kind': 'observation', 'evidence_ids': ['R1']},
                 {'id': 'future', 'kind': 'unknown', 'evidence_ids': []}]
        reviews = [{'source_group_id': 'S1', 'role': 'baseline', 'node_ids': ['prior'],
                    'reason': 'Earlier value informs a future edition; final value unknown.'}]
        audit = forecast_map.review_audit(reviews, groups, nodes)
        self.assertEqual(audit['missing_source_group_ids'], [])
        self.assertEqual(audit['unreviewed_observation_ids'], [])
        self.assertFalse(audit['meaning_verified'])

    def test_missing_or_duplicate_review_remains_advisory(self):
        groups = [{'source_group_id': 'S1', 'evidence_ids': []}]
        review = {'source_group_id': 'S1', 'role': 'irrelevant', 'node_ids': [], 'reason': 'Different entity.'}
        for rows in (None, [review, review]):
            audit = forecast_map.review_audit(rows, groups, [])
            self.assertEqual(audit['missing_source_group_ids'], ['S1'])
            self.assertFalse(audit['acceptance_gate'])

    def test_cached_map_never_adds_review_or_scoring_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = source_bundle(); baseline = decision.prepare(bundle)['baseline']
            p = literal_proposal(bundle); p['source_reviews'] = []
            with patch.object(live_trial, 'ask_model', return_value=message(p)) as model:
                first = live_trial.map_stage(bundle, baseline, tmp, 'offline', map_protocol=forecast_map.PROTOCOL)
                second = live_trial.map_stage(bundle, baseline, tmp, 'offline', map_protocol=forecast_map.PROTOCOL)
            self.assertEqual(first, second); self.assertEqual(model.call_count, 1)


class DecisionFailureRecordTests(unittest.TestCase):
    def test_429_preserves_safe_headers_and_charges_one_attempt_without_retry(self):
        headers = Message(); headers['Retry-After'] = '30'; headers['Authorization'] = 'secret'
        error = HTTPError(decisions.ENDPOINT, 429, 'limited', headers, io.BytesIO(b'{"error":"limit"}'))
        events = []
        def observer(stage, record, token=None):
            events.append((stage, copy.deepcopy(record))); return 'receipt'
        with patch.object(decisions, 'urlopen', side_effect=error) as http:
            with self.assertRaisesRegex(RuntimeError, '429'):
                decisions.decide({}, {}, 'offline', observer)
        self.assertEqual(http.call_count, 1)
        self.assertEqual([e[0] for e in events], ['reserve', 'complete'])
        final = events[-1][1]
        self.assertEqual(final['failure']['retry_after_seconds'], 30)
        self.assertEqual(final['failure']['kind'], 'rate_or_quota_limit')
        self.assertFalse(final['failure']['automatic_retry'])
        self.assertNotIn('authorization', final['response_headers'])
        self.assertNotIn('secret', json.dumps(final))

    def test_unknown_deadline_or_invalid_header_never_invents_quota_reset(self):
        for value in (None, '-1', 'not a date'):
            failure = decisions.failure_metadata(429, {'retry-after': value})
            self.assertIsNone(failure['retry_after_seconds'])
            self.assertFalse(failure['retry_deadline_known'])
            self.assertFalse(failure['quota_reset'])


if __name__ == '__main__':
    unittest.main()
