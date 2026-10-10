"""Native-loop delegation and chronological evidence audit, without providers."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import save, load
from ForecastAgent.research_loop import full_loop_trial as trial, fusion, grounding, live_trial


def fixture():
    return {'request': {'id': '46078', 'question': 'Will a new model release?',
                        'question_type': 'binary', 'resolution_criteria': 'Official release.',
                        'resolution': 'yes'}, 'pages': {}, 'searches': [], 'exa_searches': [],
            'fetch_attempts': [], 'extract_attempts': [], 'model_attempts': []}


class FullLoopTests(unittest.TestCase):
    def test_input_is_fresh_question_only_with_explicit_policy(self):
        parent = fixture()
        request = trial.input_for(parent, '2026-10-09T00:00:00+00:00')
        self.assertNotIn('resolution', request)
        self.assertNotIn('pages', request)
        self.assertEqual(request[grounding.FIELD], grounding.POLICY)
        self.assertEqual(request[fusion.POLICY_FIELD], fusion.POLICY_NAME)
        self.assertEqual(trial.BUDGET['score_http'], 0)

    def test_new_body_requires_later_revision_and_exact_body_binding(self):
        bundle = fixture()
        bundle['research_acquisition'] = {'events': [{'index': 1, 'tool': 'fetch_page',
            'map_revision': 1, 'research_node_ids': ['gap'],
            'completed_at_utc': '2026-10-09T01:00:00+00:00',
            'new_bodies': [{'url': 'https://example.org/new', 'body_sha256': 'new', 'usable_text': True}]}]}
        def revision(number, at, body, kind='observation'):
            return {'revision': number, 'at_utc': at, 'state': {'nodes': [{'id': 'quote',
                'kind': kind, 'claim': 'literal', 'bindings': [{'url': 'https://example.org/new',
                    'body_sha256': body, 'evidence_id': 'Rexact'}]}]}}
        bundle['research_loop'] = {'events': [
            revision(1, '2026-10-09T00:00:00+00:00', 'new'),
            revision(2, '2026-10-09T00:30:00+00:00', 'new'),
            revision(2, '2026-10-09T01:01:00+00:00', 'wrong'),
            revision(2, '2026-10-09T01:01:00+00:00', 'new', 'unknown'),
            revision(3, '2026-10-09T01:02:00+00:00', 'new')]}
        result = trial.feedback_audit(bundle)
        self.assertEqual(result['post_map_body_events_bound_later'], 1)
        self.assertEqual(len(result['body_events'][0]['later_observation_bindings']), 1)
        bundle['research_loop']['events'].pop()
        self.assertEqual(trial.feedback_audit(bundle)['post_map_body_events_bound_later'], 0)

    def test_supplement_is_recorded_outside_feedback_loop(self):
        bundle = fixture()
        package = copy.deepcopy(bundle)
        package['pages']['https://example.org/report'] = {'content': 'Useful official report with dated findings. '*30}
        result = trial.feedback_audit(bundle, package)
        self.assertEqual(len(result['supplement_added_or_changed_readable_bodies']), 1)
        self.assertEqual(result['post_map_body_events_bound_later'], 0)

    def test_native_pipeline_runs_through_closure_and_completed_cache_skips_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            parent = tmp/'parent.json'
            save(parent, fixture())
            calls = []
            def pipeline(request, directory, supplement_network=False):
                calls.append('native_pipeline')
                bundle = fixture()
                bundle['request'] = request
                bundle['result'] = {'status': 'collected', 'termination_reason': 'agent_finished'}
                # Pipeline returns its terminal package, even when maps precede closure.
                save(directory/'collection/bundle.json', bundle)
                save(directory/'package.json', bundle)
                return {'state': 'complete', 'resources': {}}
            with patch.dict('os.environ', {'FORECAST_MODEL': live_trial.SUPER, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), \
                    patch.object(trial.pipeline, 'identity', return_value={'candidate_sha256': {'code': 'same'}}), \
                    patch.object(trial.pipeline, 'run', side_effect=pipeline), \
                    patch.object(trial.state, 'audit', return_value={'status': 'bound_unverified'}), \
                    patch.object(trial.fusion_trial, 'score', side_effect=AssertionError('No scoring')):
                prepared = trial.run([parent], tmp/'trial')
                self.assertEqual(prepared['rows'][0]['status'], 'prepared')
                self.assertEqual(calls, [])
                result = trial.run([parent], tmp/'trial', execute=True)
                self.assertEqual(result['rows'][0]['summary']['stop']['termination_reason'], 'agent_finished')
                self.assertEqual(result['rows'][0]['status'], 'exported')
                trial.run([parent], tmp/'trial', execute=True)
                self.assertEqual(calls, ['native_pipeline'])
                self.assertFalse(list((tmp/'trial').rglob('decision/*')))
                changed = fixture()
                changed['request']['question'] = 'Changed target'
                save(parent, changed)
                with self.assertRaisesRegex(ValueError, 'Frozen inputs'):
                    trial.run([parent], tmp/'trial', execute=True)

    def test_hard_cap_rejects_overrun(self):
        summary = {'acquisition': {'super_http': 17, 'tavily_basic': 3, 'exa': 1,
            'initial_fetch_reservations': 8, 'extract_batches': 1, 'map_revisions': 3}}
        with self.assertRaisesRegex(ValueError, 'super_http'):
            trial.verify_caps(summary)

    def test_interrupted_migration_preserves_clock_and_consumed_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);parent=tmp/'parent.json';save(parent,fixture())
            def interrupted(request,directory,supplement_network=False):
                b=fixture();b['request']=request
                b['model_attempts']=[{'status':'received','usage':{'total_tokens':100}}]
                b['searches']=[{'status':'completed','results':[]}]
                b['result']={'status':'partial','incomplete':True,'resumable':True}
                save(directory/'collection/bundle.json',b)
                return {'state':{'stage':'collection'},'resources':{}}
            with patch.dict('os.environ',{'FORECAST_MODEL':live_trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}), \
                    patch.object(trial.pipeline,'identity',return_value={'candidate_sha256':{'code':'old'}}), \
                    patch.object(trial.pipeline,'run',side_effect=interrupted), \
                    patch.object(trial.state,'audit',return_value={'status':'unbuilt'}):
                trial.run([parent],tmp/'old',execute=True)
                before=(tmp/'old/cases/46078/acquisition/collection/bundle.json').read_bytes()
                clock=(tmp/'old/cases/46078/collection-clock.json').read_bytes()
                with patch.object(trial.pipeline,'identity',return_value={'candidate_sha256':{'code':'new'}}):
                    trial.run([parent],tmp/'new',continue_from=tmp/'old')
                self.assertEqual((tmp/'new/cases/46078/acquisition/collection/bundle.json').read_bytes(),before)
                self.assertEqual((tmp/'new/cases/46078/collection-clock.json').read_bytes(),clock)
                self.assertEqual((tmp/'old/cases/46078/acquisition/collection/bundle.json').read_bytes(),before)
                saved=load(tmp/'new/cases/46078/acquisition/collection/bundle.json')
                self.assertEqual(len(saved['model_attempts']),1)
                self.assertEqual(len(saved['searches']),1)
                saved['result']={'status':'collected','incomplete':False}
                save(tmp/'old/cases/46078/acquisition/collection/bundle.json',saved)
                with self.assertRaisesRegex(ValueError,'Never reopen'):
                    trial.run([parent],tmp/'forbidden',continue_from=tmp/'old')


if __name__ == '__main__':
    unittest.main()
