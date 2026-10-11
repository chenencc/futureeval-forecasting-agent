"""Full release diagnostics/reread with matched originals and distinct forecasts."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.releases import context_decisions as release
from ForecastAgent.research_loop.release_paired_trial import paired_release, mapped_state
from ForecastAgent.tests.test_competition_mercury import bundle, response
from ForecastAgent.runtime.records import save


class ReleasePairTests(unittest.TestCase):
    def provider(self, states, insufficient=False):
        def call(state, heads, key, observer):
            states.append(copy.deepcopy(state))
            mapped = 'research_map' in state
            record = {'status': 'reserved', 'request': {'model': 'inception/mercury-decide:free',
                       'state': state, 'questions': heads}}
            token = observer('reserve', record)
            result = response(heads, insufficient=insufficient and not mapped)
            if 'event_yes' in result['answers']:
                result['answers']['event_yes']['noul'] = .7 if mapped else .3
            record.update(status='received', response=result)
            observer('complete', record, token)
            return result
        return call

    def mapped(self, child, original, heads):
        return {**copy.deepcopy(original), 'research_map': {'factual_grounding_present': True}}, {}

    def test_original_release_and_map_have_same_full_diagnostic_heads(self):
        states = []; raw = bundle()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), \
                patch('ForecastAgent.providers.decisions.decide', self.provider(states)), \
                patch('ForecastAgent.research_loop.release_paired_trial.mapped_state', self.mapped):
            result = paired_release(raw, raw, Path(tmp))
        self.assertEqual(result['original']['probability_yes'], .3)
        self.assertEqual(result['mapped']['probability_yes'], .7)
        self.assertTrue(result['independent_final_pair'])
        self.assertEqual(len(states), 2)
        self.assertEqual(states[0], {k: v for k, v in states[1].items() if k != 'research_map'})
        self.assertFalse(result['second_call_required'])

    def test_mapped_first_does_not_route_using_mapped_answers(self):
        states = []; raw = bundle(long=True)
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), \
                patch('ForecastAgent.providers.decisions.decide', self.provider(states, insufficient=True)), \
                patch('ForecastAgent.research_loop.release_paired_trial.mapped_state', self.mapped):
            result = paired_release(raw, raw, Path(tmp), mapped_first=True)
        self.assertEqual(len(result['stages']), 2)
        self.assertEqual(len(states), 4)
        self.assertFalse(result['mapped_own_routing_applied'])
        self.assertEqual([r['stage'] for r in result['stages']], ['first', 'second'])
        self.assertEqual(result['stages'][1]['mapped_diagnostic_reasons'], [])
        for a, b in zip(states[::2], states[1::2]):
            self.assertEqual({k: v for k, v in a.items() if k != 'research_map'}, b)

    def test_gap_only_map_is_not_a_second_independent_forecast(self):
        states = []; raw = bundle()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), \
                patch('ForecastAgent.providers.decisions.decide', self.provider(states)), \
                patch('ForecastAgent.research_loop.release_paired_trial.mapped_state',
                      side_effect=lambda child, original, heads: (copy.deepcopy(original), {})):
            result = paired_release(raw, raw, Path(tmp))
        self.assertEqual(len(states), 1)
        self.assertFalse(result['independent_final_pair'])
        self.assertEqual(result['original'], result['mapped'])

    def test_existing_failed_receipt_cannot_silently_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); save(root/'original/first/http/001.json', {'status': 'failed'})
            with patch('ForecastAgent.research_loop.release_paired_trial.mapped_state', self.mapped):
                with self.assertRaisesRegex(ValueError, 'unfinished stage'):
                    paired_release(bundle(), bundle(), root)

    def test_original_exception_restores_provider_seam(self):
        prior = chain.call
        with tempfile.TemporaryDirectory() as tmp, patch('ForecastAgent.research_loop.release_paired_trial.mapped_state',
                side_effect=ValueError('Invalid map binding')):
            with self.assertRaisesRegex(ValueError, 'Invalid map binding'):
                paired_release(bundle(), bundle(), Path(tmp))
        self.assertIs(chain.call, prior)

    def test_failed_map_provider_does_not_change_original_release_forecast(self):
        states = []; normal = self.provider(states)
        def fail_map(state, heads, key, observer):
            if 'research_map' in state:
                raise RuntimeError('Mapped provider unavailable')
            return normal(state, heads, key, observer)
        raw = bundle()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), \
                patch('ForecastAgent.providers.decisions.decide', fail_map), \
                patch('ForecastAgent.research_loop.release_paired_trial.mapped_state', self.mapped):
            result = paired_release(raw, raw, Path(tmp), mapped_first=True)
        self.assertEqual(result['original']['probability_yes'], .3)
        self.assertIsNone(result['mapped'])
        self.assertFalse(result['independent_final_pair'])
        self.assertEqual(result['stages'][0]['mapped_error']['type'], 'RuntimeError')

    def test_all_nonbinary_types_decode_through_release_payload_policy(self):
        for kind in ('multiple_choice', 'numeric', 'discrete', 'date'):
            states = []; raw = bundle(kind)
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), \
                    patch('ForecastAgent.providers.decisions.decide', self.provider(states)), \
                    patch('ForecastAgent.research_loop.release_paired_trial.mapped_state', self.mapped):
                result = paired_release(raw, raw, Path(tmp))
                self.assertEqual(result['original'], result['mapped'])
                self.assertTrue(result['independent_final_pair'])

    def test_oversize_map_does_not_remove_originals_to_fit(self):
        original = {'evidence': [{'text': 'literal original'}]}
        with patch('ForecastAgent.research_loop.release_paired_trial.decision.scoring_map', return_value=(
                {'factual_grounding_present': True, 'nodes': ['large'*30000]}, {})):
            state, audit = mapped_state({}, original, {})
        self.assertEqual(state, original)
        self.assertTrue(audit['scoring_map_omitted'])


if __name__ == '__main__':
    unittest.main()
