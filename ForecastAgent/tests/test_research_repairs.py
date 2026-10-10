"""Gap inventories, actionable corrections and strict cumulative arithmetic."""
import copy
import unittest

from ForecastAgent.research_loop import state, decision
from ForecastAgent.research_loop.acceptance import accept, MapAcceptanceError
from ForecastAgent.research_loop.feedback import correction
from ForecastAgent.research_loop.distribution import forecast
from ForecastAgent.tests.test_research_loop import source_bundle, proposal
from ForecastAgent.tests.test_research_live_trial import message
from ForecastAgent.research_loop.live_trial import parse_map, visible_catalog
from ForecastAgent.research_loop import live_trial
from ForecastAgent.analysis.pilot import load
from pathlib import Path
from unittest.mock import patch
import tempfile


class GapInventoryTests(unittest.TestCase):
    def test_explicit_unknown_inventory_is_delivered_without_fact_or_relation(self):
        b = source_bundle(); before = copy.deepcopy(b['pages']); p = proposal(b)
        p['nodes'] = [p['nodes'][-1]]
        p['relations'] = []
        accept(b, p)
        report = state.audit(b)
        self.assertTrue(report['usable']); self.assertTrue(report['gap_only'])
        self.assertFalse(report['factual_grounding_present'])
        packed = decision.prepare(b); notes = packed['enriched']['research_map']
        self.assertEqual(notes['status'], 'unverified_gap_inventory')
        self.assertFalse(notes['factual_grounding_present'])
        self.assertFalse(notes['relations']); self.assertEqual(before, b['pages'])
        self.assertEqual(packed['baseline'], {k: packed['enriched'][k] for k in packed['baseline']})
        child = source_bundle(); _, refs = visible_catalog(child, packed['baseline'])
        parse_map(message(p), child, [r['evidence_id'] for r in refs])

    def test_failed_observations_do_not_turn_into_gap_inventory(self):
        b = source_bundle(); before = copy.deepcopy(b); p = proposal(b)
        p['nodes'][0]['evidence_ids'] = ['invalid']
        with self.assertRaises(MapAcceptanceError): accept(b, p)
        self.assertEqual(before, b)

    def test_unknown_inventory_does_not_certify_causal_arrows_or_absence(self):
        b = source_bundle(); p = proposal(b); p['nodes'] = [p['nodes'][-1]]
        p['relations'] = [{'from_id': 'unpublished', 'to_id': 'unpublished', 'kind': 'causal',
                           'rationale': 'A missing document establishes nonoccurrence.'}]
        p['supporting_path'] = 'QUARANTINED ABSENCE CLAIM'
        report = accept(b, p)['acceptance']
        self.assertEqual(report['status'], 'gap_only')
        self.assertTrue(report['rejected'])
        notes = decision.prepare(b)['enriched']['research_map']
        self.assertNotIn('QUARANTINED', str(notes)); self.assertFalse(notes['relations'])
        b['pages']['https://example.org/report']['content'] += 'Changed.'
        self.assertFalse(state.audit(b)['usable'])

    def test_unknown_without_gap_reason_remains_invalid(self):
        b = source_bundle(); p = proposal(b); p['nodes'] = [p['nodes'][-1]]
        p['nodes'][0]['gap_reason'] = 'none'; p['relations'] = []
        with self.assertRaises(MapAcceptanceError): accept(b, p)


class CorrectionTests(unittest.TestCase):
    def test_complete_live_resume_restores_the_exact_map_journal_without_http(self):
        from ForecastAgent.analysis.pilot import save
        from ForecastAgent.tests.test_competition_mercury import response
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); b = source_bundle(); parent = root/'input.json'; save(parent, b)
            def map_provider(messages, key, **kwargs):
                row = {'status': 'reserved', 'request': {'model': live_trial.SUPER}}
                token = kwargs['observer']('reserve', row)
                kwargs['observer']('complete', {**row, 'status': 'received'}, token)
                return message(proposal(b))
            def scorer(state_input, registry, key, observer):
                answer = response(registry)
                row = {'status': 'reserved', 'request': {'state': state_input, 'questions': registry}}
                token = observer('reserve', row)
                observer('complete', {**row, 'status': 'received', 'response': answer}, token)
                return answer
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch.object(live_trial, 'ask_model', side_effect=map_provider), patch('ForecastAgent.providers.decisions.decide', side_effect=scorer):
                first = live_trial.run([parent], root/'trial', True)
            map_path = root/'trial/cases'/str(b['request']['id'])/'map/accepted-state.json'
            exact_state = map_path.read_bytes()
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch.object(live_trial, 'ask_model', side_effect=AssertionError('No map retry')), patch('ForecastAgent.providers.decisions.decide', side_effect=AssertionError('No scoring retry')):
                second = live_trial.run([parent], root/'trial', True)
            self.assertEqual(first['paired_completed'], 1)
            self.assertEqual(second['paired_completed'], 1)
            self.assertEqual(exact_state, map_path.read_bytes())

    def test_repair_identity_resume_preserves_bytes_and_rejects_changed_allocation(self):
        from ForecastAgent.research_loop.repair_trial import _freeze
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'identity.json'
            _freeze(path, {'allocation': {'super': 1, 'mercury': 1}, 'source': 'fixed'})
            before = path.read_bytes()
            _freeze(path, {'source': 'fixed', 'allocation': {'mercury': 1, 'super': 1}})
            self.assertEqual(before, path.read_bytes())
            with self.assertRaisesRegex(ValueError, 'Frozen'):
                _freeze(path, {'source': 'fixed', 'allocation': {'mercury': 1, 'super': 2}})
            self.assertEqual(before, path.read_bytes())

    def test_envelope_failure_does_not_hide_invalid_node_timing(self):
        b = source_bundle(); p = proposal(b)
        p['alternative_path'] = ''
        p['nodes'][0].update(event_time='April 2026', time_status='source_stated')
        try: accept(b, p)
        except ValueError as exc:
            feedback = correction(message(p), exc, state.catalog(b), p['nodes'][0]['evidence_ids'])
        else: self.fail('Incomplete paths must remain invalid')
        self.assertEqual(feedback['envelope_errors'][0]['field'], 'alternative_path')
        self.assertEqual(feedback['node_errors'][0]['node_id'], 'observed_report')
        self.assertEqual(feedback['node_errors'][0]['event_time'], 'April 2026')

    def test_seeded_correction_has_one_physical_attempt_and_preserves_the_seed(self):
        b = source_bundle(); common = decision.prepare(b)['baseline']; p = proposal(b)
        p['nodes'][0].update(event_time='April 2026', time_status='source_stated')
        seed = message(p); original = copy.deepcopy(seed)
        with tempfile.TemporaryDirectory() as tmp:
            def fake(messages, key, **kwargs):
                feedback = __import__('json').loads(messages[-1]['content'])
                self.assertEqual(feedback['node_errors'][0]['event_time'], 'April 2026')
                record = {'status': 'reserved', 'request': {'model': live_trial.SUPER}}
                token = kwargs['observer']('reserve', record)
                kwargs['observer']('complete', {**record, 'status': 'received'}, token)
                return message(proposal(b))
            with patch.object(live_trial, 'ask_model', side_effect=fake) as provider:
                live_trial.map_stage(b, common, tmp, 'test', seed=seed, http_cap=1)
                live_trial.map_stage(b, common, tmp, 'test', seed=seed, http_cap=1)
                self.assertEqual(provider.call_count, 1)
            self.assertEqual(load(Path(tmp)/'request.json')['http_cap'], 1)
            self.assertEqual(len(list((Path(tmp)/'http').glob('*.json'))), 1)
        self.assertEqual(seed, original)

    def test_field_feedback_contains_only_bound_visible_literal_dates(self):
        b = source_bundle()
        b['pages']['https://example.org/report']['content'] = ('On April 4, 2026 the report changed. ' * 32)
        p = proposal(b); p['nodes'][0].update(event_time='May 2026', time_status='source_stated')
        material = state.catalog(b); allowed = p['nodes'][0]['evidence_ids']
        try: accept(b, p, allowed=allowed)
        except MapAcceptanceError as exc:
            feedback = correction(message(p), exc, material, allowed)
        else: self.fail('Wrong date must remain rejected')
        detail = feedback['node_errors'][0]
        self.assertEqual(detail['node_id'], 'observed_report')
        self.assertEqual(detail['event_time'], 'May 2026')
        dates = detail['literal_dates_in_bound_visible_spans']
        self.assertEqual(dates[0]['date_phrases'], ['April 4, 2026'])
        self.assertFalse(dates[0]['role_verified'])
        self.assertFalse(feedback['new_material_added'])
        withheld = correction(message(p), ValueError('error'), material, [])
        self.assertTrue(withheld['node_errors'])
        self.assertTrue(all(not n['literal_dates_in_bound_visible_spans'] for n in withheld['node_errors']))

    def test_reference_overflow_is_explained_not_silently_truncated(self):
        b = source_bundle(); p = proposal(b)
        ref = p['nodes'][0]['evidence_ids'][0]
        p['nodes'][0]['evidence_ids'] = [ref] * 13
        try: accept(b, p)
        except MapAcceptanceError as exc:
            feedback = correction(message(p), exc, state.catalog(b), [ref])
        else: self.fail('Oversized reference list must remain rejected')
        self.assertEqual(feedback['node_errors'][0]['reference_count'], 13)
        self.assertEqual(len(p['nodes'][0]['evidence_ids']), 13)


class StableDistributionTests(unittest.TestCase):
    def spec(self, lower=False, upper=False):
        b = source_bundle('discrete')
        b['request'].update(open_lower_bound=lower, open_upper_bound=upper)
        return decision.prepare(b)['spec']

    def response(self, values):
        return {'answers': {'event_outcome': {'probabilities': values}}}

    def test_machine_precision_boundary_has_valid_closed_cdf(self):
        from ForecastAgent.analysis.distributions import validate_cdf
        b = source_bundle('discrete'); spec = self.spec()
        b['request'].update(open_lower_bound=False, open_upper_bound=False)
        values = {key: 1 / len(spec['criteria']) for key in spec['criteria']}
        result = forecast(self.response(values), spec)
        validate_cdf(result['continuous_cdf'], b['request'], clipped=True)
        self.assertEqual(result['continuous_cdf'][0], 0)
        self.assertEqual(result['continuous_cdf'][-1], 1)
        self.assertTrue(result['arithmetic_audit']['raw_values_strictly_validated'])

    def test_raw_provider_overflow_even_tiny_is_rejected(self):
        spec = self.spec(); values = dict.fromkeys(spec['criteria'], 0.)
        values[next(iter(values))] = 1.0000000000000002
        with self.assertRaisesRegex(ValueError, 'outside'): forecast(self.response(values), spec)
        values[next(iter(values))] = float('nan')
        with self.assertRaises(ValueError): forecast(self.response(values), spec)
        values[next(iter(values))] = True
        with self.assertRaises(ValueError): forecast(self.response(values), spec)

    def test_open_tail_mass_is_preserved_and_missing_bin_rejected(self):
        spec = self.spec(True, True); count = len(spec['edges']) - 1
        values = {key: .6 / count for key in spec['criteria']}
        values.update(below=.15, above=.25)
        result = forecast(self.response(values), spec)
        self.assertAlmostEqual(result['raw_cdf'][0], .15)
        self.assertAlmostEqual(result['raw_cdf'][-1], .75)
        values.pop('bin_0')
        with self.assertRaisesRegex(ValueError, 'exact supplied'): forecast(self.response(values), spec)


class ReceiptRecoveryTests(unittest.TestCase):
    def run_scoring(self, b, root):
        from ForecastAgent.tests.test_competition_mercury import response
        from ForecastAgent.providers.decisions import MODEL
        def scorer(state_input, registry, key, observer):
            answer = response(registry)
            row = {'status': 'reserved', 'request': {'model': MODEL, 'state': state_input, 'questions': registry}}
            token = observer('reserve', row)
            observer('complete', {**row, 'status': 'received', 'response': answer}, token)
            return answer
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch('ForecastAgent.providers.decisions.decide', side_effect=scorer):
            return decision.run(b, root, execute=True, arm='baseline')

    def test_metadata_difference_is_reported_without_rewriting_identity_or_request(self):
        from ForecastAgent.research_loop.recovery import replay
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); b = source_bundle(); result = self.run_scoring(b, root/'decision')
            original = (root/'decision/identity.json').read_bytes()
            b['local_debug_metadata'] = 'Changed only outside the exact decision packet'
            with patch('ForecastAgent.providers.decisions.decide', side_effect=AssertionError('No HTTP')):
                recovered = replay(b, root/'decision', root/'replay', 'baseline')
            self.assertEqual(recovered['payload'], result['payload'])
            self.assertFalse(recovered['source_bundle_identity_matches'])
            self.assertEqual(recovered['new_http_attempts'], 0)
            self.assertEqual(original, (root/'decision/identity.json').read_bytes())
            b['pages']['https://example.org/report']['content'] += 'Different target evidence.'
            with self.assertRaisesRegex(ValueError, 'packet or registry'):
                replay(b, root/'decision', root/'changed', 'baseline')

    def test_decoded_response_without_matching_received_receipt_is_rejected(self):
        from ForecastAgent.research_loop.recovery import replay
        from ForecastAgent.analysis.pilot import save
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); b = source_bundle(); self.run_scoring(b, root/'decision')
            receipt = root/'decision/baseline/http/001.json'
            row = load(receipt); row['response']['answers']['event_yes']['noul'] = .2
            save(receipt, row)
            with self.assertRaisesRegex(ValueError, 'matching received'):
                replay(b, root/'decision', root/'replay', 'baseline')


if __name__ == '__main__':
    unittest.main()
