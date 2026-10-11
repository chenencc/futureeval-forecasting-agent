"""Exercise the native collection/forecast boundary before any provider call."""
import copy
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from ForecastAgent.research_loop import release_paired_trial as paired
from ForecastAgent.tests.test_competition_mercury import bundle


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


if __name__ == '__main__':
    unittest.main()
