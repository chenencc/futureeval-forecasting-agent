"""Real release decoding on synthetic receipts; no outcome or provider requests."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.analysis.distributions import grid, range_metadata
from ForecastAgent.research_loop import evaluate_release as evaluation
from ForecastAgent.research_loop.labels import bind
from ForecastAgent.research_loop.release_paired_trial import paired_release, timed_call
from ForecastAgent.runtime.records import load, save
from ForecastAgent.tests.test_competition_mercury import bundle, response


class ReleaseEvaluationTests(unittest.TestCase):
    def setup_case(self, root, *, kind='binary', independent=True, long=False, reread=False):
        raw = bundle(kind, long=long); raw['request']['post_id'] = 17
        # A declared earlier cutoff must never replace the received-reply clock.
        raw['request']['as_of_utc'] = '2020-01-01T00:00:00Z'
        parent = root/'parent.json'; trial = root/'trial'; save(parent, raw)

        def provider(state, heads, key, observer):
            record = {'endpoint': 'https://openrouter.ai/api/alpha/decisions',
                      'request': {'model': 'inception/mercury-decide:free',
                                 'state': state, 'questions': heads}, 'status': 'reserved'}
            token = observer('reserve', record)
            result = response(heads, insufficient=reread and 'research_map' not in state)
            if 'event_yes' in result['answers']:
                result['answers']['event_yes']['noul'] = .7 if 'research_map' in state else .3
            record.update(status='received', response=result, http_status=200)
            observer('complete', record, token)
            return result

        def mapped(child, original, heads):
            return ({**copy.deepcopy(original), 'research_map': {'factual_grounding_present': True}}
                    if independent else copy.deepcopy(original)), {}

        with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), \
                patch('ForecastAgent.providers.decisions.decide', provider), \
                patch('ForecastAgent.research_loop.release_paired_trial.mapped_state', mapped):
            result = paired_release(raw, raw, trial/'analysis')
        save(trial/'result.json', result)
        save(trial/'common-first.json', result['stages'][0]['originals'])
        save(trial/'identity.json', {'protocol': 'release-schedule-map-ablation-v1',
                                    'parent_sha256': evaluation.sha(parent),
                                    'first_original_sha256': digest(result['stages'][0]['originals'])})
        save(trial/'closure.json', {'status': 'finished', 'restarts_allowed': False})
        for path in trial.rglob('client-clock.json'):
            clock = load(path)
            clock.update(started_at_utc='2026-10-11T03:00:00Z', finished_at_utc='2026-10-11T03:01:00Z')
            save(path, clock)
        return trial, parent

    def bound(self, parent, root, first='2026-10-12T03:00:00Z'):
        q = load(parent)['request']; kind = q['question_type']
        label = {'type': kind, 'value': 1} if kind == 'binary' else (
            {'type': kind, 'resolution_display': 'A'} if kind == 'multiple_choice' else
            {'type': kind, 'result_kind': 'numeric', 'value': 50.25})
        source = root/'synthetic-label-source.json'
        save(source, {'question': q, 'outcome': label})
        return bind(q, label, load(source), {'source_url': 'https://example.org/synthetic-result',
                    'source_path': str(source), 'source_sha256': evaluation.sha(source),
                    'observed_at': '2026-10-20T00:00:00Z', 'method': 'synthetic_test',
                    'first_public_outcome_at': first, 'first_public_outcome_reviewed': True})

    def sealed(self, root, trial, parent):
        path = root/'seal.json'; evaluation.seal(trial, parent, path); return path

    def test_same_originals_binary_pair_uses_received_clock_and_real_decoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root)
            binding = self.bound(parent, root); seal = self.sealed(root, trial, parent)
            with patch('socket.socket.connect', side_effect=AssertionError('No network')):
                result = evaluation.evaluate(seal, binding)
            row = result['row']
            self.assertEqual(row['evaluation_status'], 'evaluated_prospective_diagnostic')
            self.assertAlmostEqual(row['metrics']['original']['brier'], .49)
            self.assertAlmostEqual(row['metrics']['mapped']['brier'], .09)
            self.assertTrue(row['prospective_pair_eligible'])
            self.assertEqual(row['stages'][0]['original']['forecast_time'], '2026-10-11T03:01:00Z')
            self.assertEqual(result['new_http'], 0)

    def test_missing_outcome_does_not_produce_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root)
            row = evaluation.evaluate(self.sealed(root, trial, parent))['row']
            self.assertEqual(row['evaluation_status'], 'pending_outcome')
            self.assertIsNone(row['metrics'])

    def test_no_call_clock_keeps_valid_pair_historical_not_prospective(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root)
            for path in trial.rglob('client-clock.json'):
                path.unlink()
            binding = self.bound(parent, root)
            row = evaluation.evaluate(self.sealed(root, trial, parent), binding)['row']
            self.assertEqual(row['evaluation_status'], 'evaluated_historical_diagnostic')
            self.assertFalse(row['prospective_pair_eligible'])
            self.assertEqual(row['temporal']['original']['status'], 'missing_actual_call_clock')

    def test_both_arms_must_finish_before_first_public_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root)
            path = trial/'analysis/mapped/first/client-clock.json'; clock = load(path)
            clock['finished_at_utc'] = '2026-10-13T00:00:00Z'; save(path, clock)
            binding = self.bound(parent, root)
            row = evaluation.evaluate(self.sealed(root, trial, parent), binding)['row']
            self.assertFalse(row['prospective_pair_eligible'])
            self.assertEqual(row['temporal']['mapped']['status'], 'after_public_outcome')

    def test_label_requires_full_identity_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root)
            row = evaluation.evaluate(self.sealed(root, trial, parent), {'id': '7', 'value': 1})['row']
            self.assertEqual(row['evaluation_status'], 'quarantined_label')
            self.assertIsNone(row['metrics'])

    def test_gap_fallback_is_not_counted_as_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root, independent=False)
            row = evaluation.evaluate(self.sealed(root, trial, parent))['row']
            self.assertEqual(row['evaluation_status'], 'not_independent')
            self.assertFalse(row['independent_pair'])

    def test_nonbinary_decoding_and_label_metrics(self):
        for kind in ('multiple_choice', 'discrete'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); trial, parent = self.setup_case(root, kind=kind)
                binding = self.bound(parent, root)
                row = evaluation.evaluate(self.sealed(root, trial, parent), binding)['row']
                self.assertEqual(row['evaluation_status'], 'evaluated_prospective_diagnostic')
                key = 'multiclass_brier' if kind == 'multiple_choice' else 'ranked_probability_score'
                self.assertIn(key, row['metrics']['original'])

    def test_final_second_read_controls_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root, long=True, reread=True)
            row = evaluation.evaluate(self.sealed(root, trial, parent))['row']
            self.assertEqual(row['evaluation_status'], 'pending_outcome')
            self.assertEqual([r['stage'] for r in row['stages']], ['first', 'second'])

    def test_exact_release_grid_projection_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root, kind='discrete')
            raw = load(parent)
            raw['request']['scaling']['continuous_range'] = grid(range_metadata(raw['request']))
            save(parent, raw); identity = load(trial/'identity.json')
            identity['parent_sha256'] = evaluation.sha(parent); save(trial/'identity.json', identity)
            row = evaluation.evaluate(self.sealed(root, trial, parent))['row']
            self.assertEqual(row['evaluation_status'], 'pending_outcome', row.get('error'))

    def test_failed_uncompleted_stage_is_preserved_but_hidden_success_rejected(self):
        for status in ('http_error', 'received'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); trial, parent = self.setup_case(root)
                save(trial/'analysis/original/second/http/001.json', {'status': status})
                row = evaluation.evaluate(self.sealed(root, trial, parent))['row']
                expected = 'pending_outcome' if status == 'http_error' else 'quarantined_pair'
                self.assertEqual(row['evaluation_status'], expected)
                if status == 'http_error':
                    self.assertEqual(len(row['failed_uncompleted_stages']), 1)

    def test_wrong_official_status_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root)
            status = root/'official.json'; save(status, {'id': '8', 'post_id': 17})
            path = root/'seal.json'; evaluation.seal(trial, parent, path, status)
            self.assertEqual(evaluation.evaluate(path)['row']['evaluation_status'], 'quarantined_pair')

    def test_changed_parent_or_trial_after_seal_is_quarantined(self):
        for target in ('parent', 'trial'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); trial, parent = self.setup_case(root)
                path = self.sealed(root, trial, parent)
                save(parent if target == 'parent' else trial/'extra-unrecorded.json', {'changed': True})
                row = evaluation.evaluate(path)['row']
                self.assertEqual(row['evaluation_status'], 'quarantined_pair')

    def test_receipt_forecast_or_original_tampering_is_caught_even_before_seal(self):
        for target in ('receipt', 'forecast', 'original', 'clock', 'registry'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); trial, parent = self.setup_case(root)
                if target in ('receipt', 'registry'):
                    path = trial/'analysis/original/first/http/001.json'; record = load(path)
                    record['request']['model' if target == 'receipt' else 'questions'] = 'wrong'
                    save(path, record)
                elif target == 'forecast':
                    path = trial/'result.json'; record = load(path)
                    record['original']['probability_yes'] = .9; save(path, record)
                elif target == 'original':
                    path = trial/'common-first.json'; record = load(path)
                    record['evidence'][0]['text'] = 'invented source'; save(path, record)
                else:
                    path = trial/'analysis/original/first/client-clock.json'; record = load(path)
                    record['receipt_sha256'] = 'wrong'; save(path, record)
                row = evaluation.evaluate(self.sealed(root, trial, parent))['row']
                self.assertEqual(row['evaluation_status'], 'quarantined_pair')

    def test_seal_does_not_overwrite_originals_or_existing_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial, parent = self.setup_case(root)
            with self.assertRaises(ValueError):
                evaluation.seal(trial, parent, trial/'seal.json')
            path = self.sealed(root, trial, parent)
            with self.assertRaises(ValueError):
                evaluation.seal(trial, parent, path)

    def test_cached_reply_never_gets_a_new_client_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); save(root/'http/001.json', {'status': 'received'})
            timed_call(lambda *args: {'cached': True}, {}, root, {})
            self.assertFalse((root/'client-clock.json').exists())

    def test_failure_clock_keeps_failed_receipt_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); calls = []
            def fail(*args):
                calls.append(1); save(root/'http/001.json', {'status': 'http_error', 'request': {}})
                raise RuntimeError('503')
            with self.assertRaisesRegex(RuntimeError, '503'):
                timed_call(fail, {}, root, {})
            self.assertEqual(len(calls), 1)
            self.assertEqual(load(root/'client-clock.json')['receipt_status'], 'http_error')


if __name__ == '__main__':
    unittest.main()
