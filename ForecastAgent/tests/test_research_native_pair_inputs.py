"""Exercise the native collection/forecast boundary before any provider call."""
import copy
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from ForecastAgent.research_loop import release_paired_trial as paired
from ForecastAgent.tests.test_competition_mercury import bundle, response


class NativePairInputTests(unittest.TestCase):
    def prepare(self, raw, **kwargs):
        with patch.object(paired, 'pinned_reader', return_value=nullcontext()), \
             patch.object(paired, 'verify_control', return_value={'fixture': 'frozen'}), \
             patch('urllib.request.urlopen', side_effect=AssertionError('No network')):
            return paired.native_pair_inputs(raw, raw, 'offline-control', **kwargs)

    def test_large_native_execution_contract_is_removed_before_real_selection(self):
        raw = bundle()
        raw['request']['capability_code_identity'] = {'fixture': 'x' * 30000}
        parent = copy.deepcopy(raw)
        heads, _ = paired.registry(raw['request'])
        with self.assertRaisesRegex(ValueError, 'Existing state exceeds'):
            paired.release.select_first(raw, heads)
        projected, registry, _, common, mapped, audit = self.prepare(raw)
        self.assertEqual(raw, parent)
        self.assertNotIn('capability_code_identity', projected['request'])
        self.assertEqual(common['question'], projected['request'])
        self.assertTrue(common['evidence'])
        self.assertEqual(common, mapped)
        self.assertEqual(registry, heads)
        self.assertEqual(audit['new_http'], 0)
        self.assertFalse(audit['full_release_reread_schedule_comparison'])

    def test_unknown_large_platform_field_remains_a_real_preflight_failure(self):
        raw = bundle()
        raw['request']['new_platform_contract'] = {'literal': 'x' * 30000}
        parent = copy.deepcopy(raw)
        with self.assertRaisesRegex(ValueError, 'Existing state exceeds'):
            self.prepare(raw)
        self.assertEqual(raw, parent)

    def test_same_originals_gate_rejects_a_mutated_mapped_arm(self):
        raw = bundle()
        def altered(view, package, seed, heads):
            mapped = copy.deepcopy(seed)
            mapped['question']['resolution_criteria'] = 'Changed rule'
            return seed, mapped, {}
        with patch('ForecastAgent.research_loop.shared_map_input.align', altered):
            with self.assertRaisesRegex(ValueError, 'identical originals'):
                self.prepare(raw)

    def test_invalid_seed_caps_fail_without_truncating_rules(self):
        for value in (True, 0, -1, paired.chain.FIRST_BYTES + 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'seed byte limit'):
                self.prepare(bundle(), first_original_limit=value)

    def test_received_binary_reply_has_both_raw_and_clipped_values(self):
        question = bundle()['request']
        heads, spec = paired.registry(question)
        result = paired.forecast_result(response(heads), question, spec)
        self.assertEqual(result['distribution']['raw_probability_yes'], 1.)
        self.assertEqual(result['payload']['probability_yes'], .98)
        self.assertTrue(result['distribution']['payload_format_valid'])

    def test_typed_received_replies_use_their_exact_distribution_registry(self):
        for kind in ('multiple_choice', 'numeric', 'discrete', 'date'):
            with self.subTest(kind=kind):
                question = bundle(kind)['request']
                heads, spec = paired.registry(question)
                result = paired.forecast_result(response(heads), question, spec)
                self.assertTrue(result['distribution']['payload_format_valid'])
                self.assertEqual(result['payload']['question'], int(question['id']))

    def test_type_spec_mismatch_fails_locally(self):
        question = bundle('multiple_choice')['request']
        heads, _ = paired.registry(question)
        with self.assertRaisesRegex(ValueError, 'registry disagree'):
            paired.forecast_result(response(heads), question, None)


if __name__ == '__main__':
    unittest.main()
