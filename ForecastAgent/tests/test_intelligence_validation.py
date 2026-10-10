"""Behavioral checks for map integration, contamination gates and sealed results."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.intelligence import research_map, validation as v
from ForecastAgent.research_loop.labels import bind
from ForecastAgent.research_loop.acceptance import accept
from ForecastAgent.research_loop import simple_map
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_simple_map import literal_proposal

NOW = '2026-10-10T03:00:00Z'
LATER = '2026-10-11T03:00:00Z'


def question():
    return {'id': '1', 'question_type': 'binary', 'question': 'Will the launch occur?',
            'resolution_criteria': 'Use the official launch report.'}


def pair(mapped=True):
    original = {'question': question(), 'sources': [{'source_id': 'S1', 'capture_time': '2026-10-10T02:00:00Z'}],
                'evidence': [{'source_id': 'S1', 'text': 'The launch is scheduled tomorrow.'}]}
    second = copy.deepcopy(original)
    if mapped:
        second['research_map'] = {'nodes': [], 'truth_verified': False}
    return {'original': {'state': original, 'questions': {'event_yes': {}}},
            'mapped': {'state': second, 'questions': {'event_yes': {}}}}


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'cases' / '1'
        self.configuration = {'decision_model': 'fixed-test-model', 'code_sha256': 'fixed-code'}

    def tearDown(self):
        self.tmp.cleanup()

    def freeze(self, p=None, mode='prospective_shadow'):
        receipt = {'question_id': '1', 'status': 'open', 'outcome_known': False,
                   'observed_at_utc': '2026-10-10T02:50:00Z', 'source_url': 'https://example.org/status',
                   'source_sha256': 'a' * 64}
        with patch.object(v, 'utc_now', return_value=NOW):
            return v.freeze_case(question(), pair() if p is None else p, self.folder,
                                 cutoff_utc=NOW, mode=mode, configuration=self.configuration,
                                 status_receipt=receipt if mode == 'prospective_shadow' else None)

    def seal(self, arm='original', p=0.6):
        with patch.object(v, 'utc_now', return_value='2026-10-10T03:01:00Z'):
            return v.seal_result(self.folder, arm, {'status': 'completed', 'payload': {'probability_yes': p}})

    def label(self, available=LATER):
        label = {'type': 'binary', 'value': 1}
        source = self.root / 'isolated-label-source.json'
        reference = {'question': question(), 'outcome': label}
        save(source, reference)
        provenance = {'source_url': 'https://example.org/result', 'observed_at': LATER, 'method': 'official_archive',
                      'source_path': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                      'information_available_at_utc': available, 'information_time_source_url': 'https://example.org/publication'}
        return bind(question(), label, reference, provenance)

    def test_prospective_pending_then_paired_scoring(self):
        self.freeze(); self.seal(); self.seal('mapped', 0.8)
        self.assertTrue(v.evaluate_case(self.folder, None)['pending_resolution'])
        row = v.evaluate_case(self.folder, self.label())
        self.assertTrue(row['eligible_forecast_evaluation'])
        self.assertAlmostEqual(row['forecast_scores']['original']['brier'], 0.16)
        self.assertAlmostEqual(row['difference_mapped_minus_original']['brier'], -0.12)

    def test_duplicate_questions_cannot_overweight_a_cohort(self):
        self.freeze()
        other = self.folder.parent / 'duplicate'
        other.mkdir()
        for path in self.folder.glob('*.json'):
            (other / path.name).write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'Duplicate questions'):
            v.evaluate(self.folder.parent, {})

    def test_changed_input_is_rejected_before_label_join(self):
        self.freeze()
        value = load(self.folder / 'original-input.json')
        value['state']['evidence'][0]['text'] = 'Later result inserted.'
        save(self.folder / 'original-input.json', value)
        with self.assertRaisesRegex(ValueError, 'Sealed model input changed'):
            v.evaluate_case(self.folder, None)

    def test_native_capture_metadata_is_used_without_rewriting_originals(self):
        p = pair()
        for arm in v.ARMS:
            source = p[arm]['state']['sources'][0]
            source['capture_metadata'] = {'retrieved_at_utc': source.pop('capture_time')}
        self.assertEqual(self.freeze(p)['temporal_issues'], [])
        self.assertNotIn('capture_time', load(self.folder / 'original-input.json')['state']['sources'][0])

    def test_conflicting_capture_times_stay_excluded(self):
        p = pair()
        for arm in v.ARMS:
            p[arm]['state']['sources'][0]['capture_metadata'] = {'retrieved_at_utc': LATER}
        issues = self.freeze(p)['temporal_issues']
        self.assertIn('capture_time_conflict', [i['reason'] for i in issues])

    def test_categorical_payload_requires_exact_options_and_unit_mass(self):
        q = {'question_type': 'multiple_choice', 'options': ['A', 'B']}
        v.validate_payload(q, {'probability_yes_per_category': {'A': 0.3, 'B': 0.7}})
        with self.assertRaises(ValueError):
            v.validate_payload(q, {'probability_yes_per_category': {'A': 0.3, 'C': 0.7}})
        with self.assertRaises(ValueError):
            v.validate_payload(q, {'probability_yes_per_category': {'A': 0.3, 'B': 0.6}})

    def test_closed_unresolved_research_does_not_enable_submission(self):
        receipt = {'question_id': '1', 'status': 'closed', 'outcome_known': False,
                   'observed_at_utc': NOW, 'source_url': 'https://example.org/status', 'source_sha256': 'a' * 64}
        with patch.object(v, 'utc_now', return_value=NOW):
            manifest = v.freeze_case(question(), pair(), self.folder, cutoff_utc=NOW,
                mode='prospective_shadow', configuration=self.configuration, status_receipt=receipt)
        self.assertFalse(manifest['submission_enabled'])
        receipt['status'] = 'resolved'
        with self.assertRaises(ValueError):
            v.freeze_case(question(), pair(), self.root / 'other', cutoff_utc=NOW,
                mode='prospective_shadow', configuration=self.configuration, status_receipt=receipt)

    def test_missing_rules_are_explicit_primary_metric_exclusion(self):
        q = question(); q['resolution_criteria'] = ''; q['official_rules_available'] = False
        p = pair()
        for arm in v.ARMS:
            p[arm]['state']['question'] = copy.deepcopy(q)
        with patch.object(v, 'utc_now', return_value=NOW):
            manifest = v.freeze_case(q, p, self.folder, cutoff_utc=NOW,
                mode='retrospective_diagnostic', configuration=self.configuration)
        self.assertIn('official_resolution_rules_unavailable', [i['reason'] for i in manifest['temporal_issues']])

    def test_continuous_payload_requires_frozen_scale_and_monotonicity(self):
        q = {'question_type': 'numeric', 'scaling': {'range_min': 0, 'range_max': 10},
             'inbound_outcome_count': 20, 'open_lower_bound': True, 'open_upper_bound': True}
        n = len(v.grid(v.range_metadata(q)))
        cdf = [i / (n - 1) for i in range(n)]
        v.validate_payload(q, {'continuous_cdf': cdf})
        with self.assertRaises(ValueError):
            v.validate_payload(q, {'continuous_cdf': cdf[:-1]})
        cdf[1], cdf[2] = cdf[2], cdf[1]
        with self.assertRaises(ValueError):
            v.validate_payload(q, {'continuous_cdf': cdf})

    def test_old_resolved_material_is_diagnostic_even_with_good_score(self):
        self.freeze(mode='retrospective_diagnostic'); self.seal(); self.seal('mapped', 0.98)
        row = v.evaluate_case(self.folder, self.label())
        self.assertFalse(row['eligible_forecast_evaluation'])
        self.assertIn('diagnostic_scores', row)
        self.assertNotIn('forecast_scores', row)

    def test_platform_resolution_after_prediction_cannot_hide_prior_public_answer(self):
        self.freeze(); self.seal(); self.seal('mapped', 0.8)
        row = v.evaluate_case(self.folder, self.label('2026-10-09T01:00:00Z'))
        self.assertIn('outcome_known_before_prediction_sealed', row['exclusion_reasons'])

    def test_unknown_first_public_time_excluded(self):
        self.freeze(); self.seal(); self.seal('mapped', 0.8)
        row = v.evaluate_case(self.folder, self.label(None))
        self.assertIn('first_public_outcome_time_unverified', row['exclusion_reasons'])

    def test_old_publication_does_not_override_current_capture(self):
        p = pair()
        for arm in p:
            p[arm]['state']['sources'][0].update(capture_time=LATER, publication_time='2020-01-01')
        self.freeze(p); self.seal(); self.seal('mapped', 0.8)
        row = v.evaluate_case(self.folder, self.label('2026-10-12T00:00:00Z'))
        self.assertIn('captured_after_cutoff', row['exclusion_reasons'])

    def test_no_survivor_filter_for_missing_or_failed_arm(self):
        self.freeze(); self.seal()
        with patch.object(v, 'utc_now', return_value='2026-10-10T03:01:00Z'):
            v.seal_result(self.folder, 'mapped', {'status': 'failed', 'error': 'provider unavailable'})
        report = v.evaluate(self.root / 'cases', {'1': self.label()})
        self.assertEqual(report['registered_n'], 1)
        self.assertEqual(report['incomplete_pairs_n'], 1)
        self.assertEqual(report['by_type']['binary']['eligible_paired_n'], 0)

    def test_fallback_is_not_counted_as_map_improvement(self):
        self.freeze(pair(False)); self.seal(); self.seal('mapped', 0.6)
        row = v.evaluate_case(self.folder, self.label())
        self.assertIn('original_only_fallback_not_map_experiment', row['exclusion_reasons'])

    def test_resume_cannot_change_model_inputs_or_sealed_predictions(self):
        initial = self.freeze(); self.seal()
        self.assertEqual(self.freeze(), initial)
        self.assertEqual(self.seal()['result']['payload']['probability_yes'], 0.6)
        with self.assertRaisesRegex(ValueError, 'cannot be replaced'):
            self.seal(p=0.7)
        path = self.folder / 'original-input.json'
        changed = load(path); changed['state']['evidence'][0]['text'] = 'Changed after scoring.'; save(path, changed)
        with self.assertRaisesRegex(ValueError, 'input changed'):
            v.evaluate_case(self.folder, None)

    def test_model_change_and_outcome_fields_rejected_before_inference(self):
        self.freeze()
        self.configuration['decision_model'] = 'another-model'
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.freeze()
        p = pair()
        for arm in p:
            p[arm]['state']['resolution'] = 1
        with self.assertRaisesRegex(ValueError, 'Outcome labels'):
            self.freeze(p)

    def test_pair_cannot_hide_a_question_or_original_text_change(self):
        p = pair(); p['mapped']['state']['evidence'][0]['text'] = 'Extra outcome information.'
        with self.assertRaisesRegex(ValueError, 'identical originals'):
            self.freeze(p)
        p = pair()
        for arm in p:
            p[arm]['state']['question']['id'] = '99'
        with self.assertRaisesRegex(ValueError, 'question differs'):
            self.freeze(p)

    def test_invalid_probability_and_zero_log_loss_are_handled(self):
        self.freeze()
        with self.assertRaisesRegex(ValueError, 'Invalid probability'):
            self.seal(p=float('nan'))
        self.seal(p=0.0); self.seal('mapped', 1.0)
        row = v.evaluate_case(self.folder, self.label())
        self.assertTrue(row['forecast_scores']['original']['log_loss'] > 20)


class MapIntegrationTests(unittest.TestCase):
    def test_enable_keeps_question_models_and_budgets(self):
        q = {**question(), 'model': 'selected', 'maximum_fetches': 8}
        before = copy.deepcopy(q)
        enabled = research_map.enable(q)
        self.assertEqual(q, before)
        self.assertEqual(enabled['maximum_fetches'], 8)
        self.assertEqual(enabled['model'], 'selected')
        self.assertIn('research_gap_policy', enabled)

    def test_bound_map_projection_and_admission_keep_shared_originals(self):
        bundle = source_bundle(); accept(bundle, literal_proposal(bundle), map_protocol=simple_map.PROTOCOL)
        with tempfile.TemporaryDirectory() as tmp:
            report = research_map.project(bundle, copy.deepcopy(bundle), tmp)
            self.assertTrue(report['map_delivered'])
            a = load(Path(tmp) / 'original-input.json')['state']
            b = load(Path(tmp) / 'mapped-input.json')['state']
            self.assertEqual({k: value for k, value in b.items() if k != 'research_map'}, a)
            self.assertEqual(report['model_calls'], 0)
        view = copy.deepcopy(bundle); view['pages'] = {}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(research_map.project(bundle, view, tmp)['status'], 'needs_material_recovery')
