"""Two-route failure isolation, supplemental provenance and paired scoring."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ForecastAgent.analysis import referenced
from ForecastAgent.analysis.pilot import digest, save, load
from ForecastAgent.analysis.ensemble import compare
from ForecastAgent.analysis.evaluation import evaluate
from ForecastAgent.analysis.inputs import resolve_bundle
from ForecastAgent.tests import test_referenced_analysis


class AnalysisRoutesTests(unittest.TestCase):
    def setUp(self):
        test_referenced_analysis.ReferencedAnalysisTests.setUp(self)
        self.calls = {'reasoning': 0, 'decision': 0}

    def reasoning(self, messages, key, observer, **kwargs):
        self.calls['reasoning'] += 1
        message = {'tool_calls': [{'function': {'name': 'record_analysis', 'arguments': json.dumps(self.report)}}]}
        record = {'status': 'reserved', 'request': {'model': 'offline-model:free'}}
        token = observer('reserve', record)
        record.update(status='received', response={'choices': [{'message': message}], 'usage': {'total_tokens': 12}})
        observer('complete', record, token)
        return message

    def decision(self, state, questions, key, observer):
        self.calls['decision'] += 1
        self.assertNotIn('reasoning_probability_yes', state['analysis'])
        response = {'answers': {'event_yes': {'noul': .8}, 'evidence_sufficiency': {'score': 1.0}}}
        record = {'status': 'reserved', 'request': {'model': 'inception/mercury-decide:free'}}
        token = observer('reserve', record)
        record.update(status='received', response=response)
        observer('complete', record, token)
        return response

    def inputs(self, directory):
        root = Path(directory) / 'source'
        campaign = {'tasks': {'1': {'status': 'closed_with_gaps'}}}
        save(root / 'campaign.json', campaign)
        save(root / 'tasks/1/bundle.json', self.bundle)
        archive = Path(directory) / 'source.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('campaign.json', json.dumps(campaign))
            z.writestr('tasks/1/bundle.json', json.dumps(self.bundle))
        labels = Path(directory) / 'labels.jsonl'
        labels.write_text(json.dumps({'source': 'metaculus', 'id': '1', 'resolution': {'resolved': True, 'resolved_to': 1}}), encoding='utf-8')
        return root, Path(directory) / 'analysis', archive, labels

    def contexts(self):
        return (patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline-placeholder', 'FORECAST_MODEL': 'offline-model:free'}),
                patch('ForecastAgent.providers.ultra.ask_ultra', side_effect=self.reasoning))

    def test_both_routes_paired_scoring_and_no_extra_fusion_calls(self):
        with tempfile.TemporaryDirectory() as d:
            root, output, archive, labels = self.inputs(d)
            env, backend = self.contexts()
            with env, backend, patch.object(referenced, 'decide', side_effect=self.decision):
                referenced.run(root, output, ['1'])
                referenced.run(root, output, ['1'])
            self.assertEqual(self.calls, {'reasoning': 2, 'decision': 1})
            report = evaluate(output, archive, labels, Path(d) / 'report.json')
            self.assertEqual(report['route_counts'], {'reasoning': 1, 'mercury': 1, 'equal_mean': 1})
            self.assertAlmostEqual(report['paired_metrics']['equal_mean']['brier'], .16)
            self.assertFalse(report['results'][0]['route_comparison']['independent_forecasters'])
            routes = load(output / 'tasks/1/routes.json')
            routes['equal_mean_probability_yes'] = .99
            save(output / 'tasks/1/routes.json', routes)
            with self.assertRaisesRegex(ValueError, 'aggregation changed|Task result is inconsistent'):
                evaluate(output, archive, labels, Path(d) / 'tampered.json')

    def test_reasoning_only_never_calls_mercury_and_resume_keeps_mode(self):
        with tempfile.TemporaryDirectory() as d:
            root, output, archive, labels = self.inputs(d)
            env, backend = self.contexts()
            with env, backend, patch.object(referenced, 'decide', side_effect=AssertionError('Unexpected Mercury call')):
                referenced.run(root, output, ['1'], mode='reasoning_only')
                referenced.run(root, output, ['1'], mode='reasoning_only')
                with self.assertRaisesRegex(ValueError, 'experiment changed'):
                    referenced.run(root, output, ['1'], mode='both')
            self.assertEqual(self.calls['reasoning'], 2)
            report = evaluate(output, archive, labels, Path(d) / 'report.json')
            self.assertEqual(report['route_counts']['mercury'], 0)
            self.assertEqual(report['failures'], [])

    def test_mercury_failure_preserves_reasoning_and_missing_pair(self):
        def failed_decision(state, questions, key, observer):
            record = {'status': 'reserved', 'request': {'model': 'inception/mercury-decide:free'}}
            token = observer('reserve', record)
            record.update(status='failed', response={'error': {'code': 503}})
            observer('complete', record, token)
            raise RuntimeError('Provider unavailable')

        with tempfile.TemporaryDirectory() as d:
            root, output, archive, labels = self.inputs(d)
            env, backend = self.contexts()
            with env, backend, patch.object(referenced, 'decide', side_effect=failed_decision):
                with self.assertRaises(RuntimeError):
                    referenced.run(root, output, ['1'])
                with self.assertRaises(RuntimeError):
                    referenced.run(root, output, ['1'])
            self.assertEqual(self.calls['reasoning'], 2)
            self.assertEqual(len(list((output / 'tasks/1/mercury-http').glob('*.json'))), 1)
            report = evaluate(output, archive, labels, Path(d) / 'report.json')
            self.assertEqual(report['route_counts'], {'reasoning': 1, 'mercury': 0, 'equal_mean': 0})
            self.assertEqual(len(report['failures']), 1)
            self.assertEqual(report['paired_metrics'], {})

    def test_supplement_overlay_verified_during_analysis_and_evaluation(self):
        with tempfile.TemporaryDirectory() as d:
            root, output, archive, labels = self.inputs(d)
            supplement = Path(d) / 'supplement'
            save(supplement / 'manifest.json', {'ids': ['1']})
            page = {'content': self.body + '\nSupplemental observation.'}
            save(supplement / 'tasks/1/captures/page.json', page)
            child = {'task_id': '1', 'parent_bundle_json_sha256': digest(self.bundle),
                     'captures': {'https://example.org': {'readable': True, 'file': 'captures/page.json', 'json_sha256': digest(page)}},
                     'remaining_gaps': [{'url': 'https://missing.example', 'category': 'access_restricted'}],
                     'analysis_handoff': {'inventory_task_gaps': ['August missing']}}
            save(supplement / 'tasks/1/supplement.json', child)
            before = copy.deepcopy(self.bundle)
            self.assertIn('Supplemental observation.', resolve_bundle(self.bundle, '1', supplement)['pages']['https://example.org']['content'])
            self.assertEqual(self.bundle, before)
            env, backend = self.contexts()
            with env, backend:
                referenced.run(root, output, ['1'], supplement_root=supplement, mode='reasoning_only')
            report = evaluate(output, archive, labels, Path(d) / 'report.json', supplement_root=supplement)
            self.assertTrue(report['results'][0]['evidence_stats']['supplement_overlay_used'])
            self.assertTrue(load(output / 'tasks/1/evidence-packet.json')['supplement_handoff']['remaining_gaps'])
            page['content'] += 'mutated'
            save(supplement / 'tasks/1/captures/page.json', page)
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                evaluate(output, archive, labels, Path(d) / 'tampered.json', supplement_root=supplement)

    def test_unavailable_route_is_not_imputed_and_nonfinite_rejected(self):
        self.assertIsNone(compare(.4)['equal_mean_probability_yes'])
        with self.assertRaises(ValueError):
            compare(float('nan'), .5)

    def test_marked_access_interstitial_is_excluded_from_evidence(self):
        self.bundle['pages']['https://blocked.example'] = {
            'content': 'Verify you are human. ' * 200,
            'body_diagnostics': {'usable_text': False, 'state': 'access_interstitial'}}
        packet = referenced.evidence_packet(self.bundle)
        self.assertEqual(len(packet['sources']), 1)
        self.assertEqual(packet['excluded_unusable_sources'][0]['url'], 'https://blocked.example')

    def test_invalid_citations_recover_within_same_cap_and_hide_narratives(self):
        self.report['facts'][0]['evidence_refs'] = []
        observed = {}
        def score(state, questions, key, observer):
            observed.update(state)
            return self.decision(state, questions, key, observer)
        with tempfile.TemporaryDirectory() as d:
            root, output, archive, labels = self.inputs(d)
            env, backend = self.contexts()
            with env, backend, patch.object(referenced, 'decide', side_effect=score):
                referenced.run(root, output, ['1'])
            self.assertEqual(self.calls, {'reasoning': 3, 'decision': 1})
            result = load(output / 'tasks/1/result.json')
            self.assertEqual(result['status'], 'provisional')
            self.assertEqual(set(observed['analysis']), {'conditions', 'gaps'})
            self.assertEqual(result['reasoning_probability_yes'], .4)
            report = evaluate(output, archive, labels, Path(d) / 'report.json')
            self.assertEqual(report['recovered_model_results'], 1)

    def test_no_numeric_output_still_records_explicit_operational_default(self):
        def broken(messages, key, observer, **kwargs):
            record = {'status': 'reserved', 'request': {'model': 'offline-model:free'}}
            token = observer('reserve', record)
            message = {'content': 'Unable to produce a structured prediction.'}
            record.update(status='received', response={'choices': [{'message': message}]})
            observer('complete', record, token)
            return message
        with tempfile.TemporaryDirectory() as d:
            root, output, archive, labels = self.inputs(d)
            env, unused = self.contexts()
            with env, patch('ForecastAgent.providers.ultra.ask_ultra', side_effect=broken):
                with self.assertRaises(RuntimeError):
                    referenced.run(root, output, ['1'])
            result = load(output / 'tasks/1/result.json')
            self.assertEqual(result['operational_probability_yes'], .5)
            self.assertIsNone(result['reasoning_probability_yes'])
            report = evaluate(output, archive, labels, Path(d) / 'report.json')
            self.assertEqual(report['evaluated_count'], 0)
            self.assertEqual(report['operational_brier_including_explicit_defaults'], .25)
            self.assertEqual(report['task_result_coverage']['recorded'], 1)

    def test_review_service_failure_preserves_valid_first_draft(self):
        def flaky(messages, key, observer, **kwargs):
            if not self.calls['reasoning']:
                return self.reasoning(messages, key, observer, **kwargs)
            record = {'status': 'reserved', 'request': {'model': 'offline-model:free'}}
            token = observer('reserve', record)
            record.update(status='failed', response={'error': {'code': 503}})
            observer('complete', record, token)
            raise RuntimeError('Review service unavailable')
        with tempfile.TemporaryDirectory() as d:
            root, output, archive, labels = self.inputs(d)
            env, unused = self.contexts()
            with env, patch('ForecastAgent.providers.ultra.ask_ultra', side_effect=flaky), patch.object(referenced, 'decide', side_effect=self.decision):
                referenced.run(root, output, ['1'])
            self.assertFalse(load(output / 'tasks/1/quality.json')['review_completed'])
            self.assertEqual(load(output / 'tasks/1/result.json')['reasoning_probability_yes'], .4)
            self.assertEqual(len(list((output / 'tasks/1/ultra-http').glob('*.json'))), 2)


if __name__ == '__main__':
    unittest.main()
