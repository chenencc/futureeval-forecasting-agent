"""Exact original coverage, actual maps, optional pivots and bounded replay."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.research_loop import conditional, decision, live_trial, map_paired_trial as trial, state
from ForecastAgent.research_loop.forecast_brief import registry
from ForecastAgent.research_loop.target_pack import pack
from ForecastAgent.tests.test_competition_mercury import response
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_simple_map import literal_proposal
from ForecastAgent.tests.test_research_live_trial import message
from ForecastAgent.tests.test_research_conditional import plan


def inputs():
    bundle = source_bundle()
    heads, spec = registry(bundle['request'])
    common = pack(bundle, heads)[0]
    child, audit = trial.freeze_references(bundle, common)
    return bundle, common, heads, spec, child, audit


def mapped(child, common):
    p = literal_proposal(child)
    p['score_plan'] = plan()
    material, refs = live_trial.visible_catalog(child, common)
    with patch.object(decision, 'prepare', side_effect=AssertionError('Legacy original reselection is forbidden')):
        _, child = live_trial.parse_map(message(p), child, [r['evidence_id'] for r in refs],
            map_protocol=conditional.PROTOCOL, scoring_baseline=common)
    return child


class MapPairTests(unittest.TestCase):
    def test_unclosed_snapshot_delivery_preserves_originals_and_gaps(self):
        bundle = source_bundle()
        bundle['result'] = None
        bundle['gaps'] = ['Current target outcome not yet observed']
        original = copy.deepcopy(bundle)
        heads, _ = registry(bundle['request'])
        common, audit = pack(bundle, heads)
        self.assertTrue(common['evidence'])
        self.assertEqual(common['retrieval_gap_count'], 1)
        self.assertEqual(bundle, original)
        self.assertIsNone(bundle['result'])
        malformed = copy.deepcopy(bundle)
        malformed['result'] = 'completed'
        with self.assertRaisesRegex(ValueError, 'object or uninitialized'):
            pack(malformed, heads)

    def test_map_handles_cover_visible_blocks_only_and_source_text_is_unchanged(self):
        bundle, common, heads, spec, child, audit = inputs()
        material = state.catalog(child)
        self.assertEqual(audit['reference_count'], len(material['spans']))
        for span in material['spans'].values():
            self.assertEqual(span['text'], child['pages'][span['url']]['content'][span['start']:span['end']])
        self.assertEqual(child['pages'], bundle['pages'])
        self.assertFalse(bundle.get('research_loop'))
        self.assertNotIn('research_map_visible_references', bundle)

    def test_mapped_arm_has_exact_same_originals_and_target_registry(self):
        _, common, heads, spec, child, _ = inputs()
        child = mapped(child, common)
        a = trial.prepare(common, heads, spec)
        b = trial.prepare(common, heads, spec, child)
        self.assertEqual(a['questions'], b['questions'])
        self.assertTrue(all(b['state'][k] == v for k, v in common.items()))
        self.assertTrue(b['state']['research_map']['factual_grounding_present'])

    def test_conditional_heads_are_separate_and_direct_probability_survives_optional_error(self):
        _, common, heads, spec, child, _ = inputs()
        child = mapped(child, common)
        direct = trial.prepare(common, heads, spec, child)
        pivot = trial.prepare(common, heads, spec, child, conditional_heads=True)
        self.assertEqual(set(direct['questions']), {'event_yes'})
        self.assertIn('target_given_not_pivot', pivot['questions'])
        raw = response(pivot['questions'])
        raw['answers'].pop('pivot_usable')
        result = conditional.evaluate(pivot, raw)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['conditional']['status'], 'invalid_optional_answers')

    def test_invalid_windows_are_rejected_and_material_hash_binds_coordinates(self):
        _, common, _, _, child, _ = inputs()
        original = state.catalog(child)['material_sha256']
        changed = copy.deepcopy(child)
        changed['research_map_visible_references'][0]['end'] -= 1
        self.assertNotEqual(state.catalog(changed)['material_sha256'], original)
        for mutation in ('hash', 'overlap', 'unavailable'):
            b = copy.deepcopy(child)
            if mutation == 'hash': b['research_map_visible_references'][0]['body_sha256'] = 'wrong'
            if mutation == 'overlap': b['research_map_visible_references'].append(copy.deepcopy(b['research_map_visible_references'][0]))
            if mutation == 'unavailable': b['research_map_visible_references'][0]['url'] = 'https://unavailable.example'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): state.catalog(b)

    def test_changed_original_text_and_existing_ledger_cannot_be_renewed(self):
        bundle, common, _, _, child, _ = inputs()
        changed = copy.deepcopy(common); changed['evidence'][0]['text'] += 'invented'
        with self.assertRaises(ValueError): trial.freeze_references(bundle, changed)
        with self.assertRaisesRegex(ValueError, 'never renew'): trial.freeze_references(child, common)

    def test_no_grounded_map_retains_original_state_without_fabricating_conditions(self):
        _, common, heads, spec, child, _ = inputs()
        prepared = trial.prepare(common, heads, spec, child, conditional_heads=True)
        self.assertEqual(prepared['state'], common)
        self.assertEqual(prepared['plan']['status'], 'direct')

    def test_one_request_per_arm_resume_preserves_usage_and_failed_requests(self):
        _, common, heads, spec, _, _ = inputs()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            def fake(state, questions, key, observer, **kwargs):
                record = {'status': 'reserved', 'request': {'model': trial.decision_http.MODEL}}
                token = observer('reserve', record)
                observer('complete', {**record, 'status': 'received', 'response': response(questions)}, token)
                return response(questions)
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test'}), patch.object(trial.decision_http, 'decide', side_effect=fake) as provider:
                trial.score(trial.prepare(common, heads, spec), folder)
                trial.score(trial.prepare(common, heads, spec), folder)
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(len(trial.receipts(folder)), 1)
            self.assertEqual(load(folder/'result.json')['status'], 'completed')


if __name__ == '__main__': unittest.main()
