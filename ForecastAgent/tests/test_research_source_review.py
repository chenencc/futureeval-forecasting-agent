"""Actual post-stage source routing, failure isolation and immutable ledgers."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import post_supplement as post
from ForecastAgent.tests.test_research_dispatch import fresh
from ForecastAgent.tests.test_research_gap_feedback import initial


class SourceReviewTests(unittest.TestCase):
    def test_final_review_shares_root_target_and_information_state_guide(self):
        from ForecastAgent.research_loop import source_review, reference_map, predictive_focus, target_logic
        with tempfile.TemporaryDirectory() as root:
            t = self.fixture(root)
            t.bundle['request'].update({reference_map.FIELD: reference_map.POLICY,
                predictive_focus.FIELD: predictive_focus.POLICY,
                target_logic.FIELD: target_logic.POLICY,
                'forecast_target_registry_policy':'question_and_research_targets_v1'})
            url = 'https://example.org/a'
            reading = t.execute('inspect_research_state', {'url':url,'limit':3}, '')
            choice = {'url':url, 'revision':t.bundle['research_loop']['revision'],
                'material_sha256':source_review.state.catalog(t.bundle,t.cutoff)['material_sha256'],
                'material_id': next(m['material_id'] for m in
                source_review.gap_feedback.pending(t) if m['url']==url)}
            messages = source_review.messages(t, reading, choice)
            self.assertIn(reference_map.target_prompt(t.bundle),messages[0]['content'])
            self.assertEqual(messages[0]['content'].count('Predictive focus:'),1)
            self.assertIn('future_unknown',messages[0]['content'])
            self.assertIn('rather than its final',messages[0]['content'])
            self.assertNotIn('Copy material_sha256',messages[0]['content'])
            self.assertNotIn('Use frozen T IDs in target_links.',messages[0]['content'])
            self.assertEqual(json.loads(messages[1]['content'])['reading']['evidence'],reading['evidence'])

    def fixture(self, root):
        t = fresh(root); initial(t)
        for suffix in ('a', 'b'):
            t.bundle['pages']['https://example.org/' + suffix] = {
                'content': ('A report about another company and a separate period; '
                            'its applicability remains uncertain. ' + suffix + '\n') * 12}
        return t

    def model(self, seen, dispositions, mutate=None):
        def reply(messages, key, **kwargs):
            data = json.loads(messages[1]['content']); seen.append(data)
            scope = data['scheduled_source']
            self.assertEqual({s['url'] for s in data['reading']['evidence']}, {scope['url']})
            schema = kwargs['tools'][0]['function']['parameters']
            self.assertNotIn('expected_revision', schema['properties'])
            self.assertNotIn('material_sha256', schema['properties'])
            self.assertNotIn('Copy material_sha256', messages[0]['content'])
            disposition = dispositions[len(seen)-1]
            review = {'material_id': scope['material_id'], 'disposition': disposition,
                      'evidence_ids': scope['inspected_reference_ids'][:1], 'node_ids': [],
                      'gap_ids': [], 'related_material_ids': [],
                      'effect': 'unknown' if disposition == 'deferred' else 'no_change',
                      'reason': 'Delivered text needs more interpretation.' if disposition == 'deferred'
                                else 'The delivered text concerns another company and period.'}
            args = {'revision_kind': 'material_update', 'update_mode': 'merge',
                    'nodes': [], 'relations': [], 'material_requests': [], 'retired_node_ids': [],
                    'supporting_path': '', 'alternative_path': '',
                    'revision_reason': 'Review this delivered source; retain the existing graph.',
                    'material_reviews': [review]}
            kinds = schema['properties']['revision_kind'].get('enum')
            if kinds: args['revision_kind'] = kinds[0]
            if mutate: mutate(args, data, len(seen))
            observer = kwargs['observer']; record = {'status': 'reserved', 'request': {'model': 'fixture'}}
            token = observer('reserve', record)
            record.update(status='received', response={'usage': {'total_tokens': 10}})
            observer('complete', record, token)
            return {'tool_calls': [{'function': {'name': 'update_research_state',
                                                 'arguments': json.dumps(args)}}]}
        return reply

    def test_deferral_does_not_starve_next_source_or_repeat_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = self.fixture(tmp); frozen = copy.deepcopy(t.bundle); seen = []
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'fixture'}), patch.object(
                    post, 'ask_model', side_effect=self.model(seen, ['deferred', 'irrelevant'])):
                child, report = post.run(frozen, Path(tmp)/'post', http_cap=4)
                again, cached = post.run(frozen, Path(tmp)/'post', http_cap=4)
            self.assertEqual(len(seen), 2); self.assertEqual(report, cached); self.assertEqual(child, again)
            self.assertEqual([x['scheduled_source']['url'] for x in seen],
                             ['https://example.org/a', 'https://example.org/b'])
            self.assertEqual(report['material_processing']['pending_material_count'], 1)
            self.assertEqual(child['research_loop']['current']['nodes'], frozen['research_loop']['current']['nodes'])
            for key in post.PRESERVED: self.assertEqual(child.get(key), frozen.get(key))
            self.assertEqual(report['usage']['totals']['http_attempts'], 2)

    def test_wrong_reference_is_rejected_before_edits_and_next_source_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = self.fixture(tmp); frozen = copy.deepcopy(t.bundle); seen = []
            def mutate(args, data, index):
                if index == 1:
                    args['material_reviews'][0]['evidence_ids'] = ['R-not-this-source']
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'fixture'}), patch.object(
                    post, 'ask_model', side_effect=self.model(seen, ['irrelevant', 'irrelevant'], mutate)):
                child, report = post.run(frozen, Path(tmp)/'post', http_cap=4)
            self.assertEqual(len(seen), 2)
            self.assertFalse(report['steps'][0]['accepted']); self.assertTrue(report['steps'][1]['accepted'])
            self.assertEqual(report['material_processing']['pending_material_count'], 1)
            self.assertEqual(child['research_loop']['current']['nodes'], frozen['research_loop']['current']['nodes'])

    def test_shared_one_http_limit_leaves_second_source_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = self.fixture(tmp); seen = []
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'fixture'}), patch.object(
                    post, 'ask_model', side_effect=self.model(seen, ['irrelevant'])):
                _, report = post.run(t.bundle, Path(tmp)/'post', http_cap=1)
            self.assertEqual(len(seen), 1)
            self.assertEqual(report['usage']['totals']['http_attempts'], 1)
            self.assertEqual(report['material_processing']['pending_material_count'], 1)
            self.assertEqual(report['status'], 'model_http_cap_exhausted')

    def test_rewrite_of_program_frame_is_never_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = self.fixture(tmp); seen = []
            def mutate(args, data, index): args['expected_revision'] = 999
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'fixture'}), patch.object(
                    post, 'ask_model', side_effect=self.model(seen, ['irrelevant'], mutate)):
                child, report = post.run(t.bundle, Path(tmp)/'post', http_cap=1)
            self.assertFalse(report['steps'][0]['accepted'])
            self.assertEqual(report['material_processing']['pending_material_count'], 2)
            self.assertEqual(child['research_loop']['current']['nodes'], t.bundle['research_loop']['current']['nodes'])

    def test_context_projection_preserves_literal_spans_cursors_and_unknown_fields(self):
        from ForecastAgent.research_loop import source_review, dispatch, state, gap_feedback
        with tempfile.TemporaryDirectory() as tmp:
            t = self.fixture(tmp)
            source = gap_feedback.pending(t)[0]
            reading = t.execute('inspect_research_state', {'url': source['url'], 'limit': 3}, '')
            reading['future_extension'] = {'literal_unit': 'unknown'}
            before = copy.deepcopy(reading)
            choice = {'phase': 'process_update', 'material_id': source['material_id'],
                      'url': source['url'], 'revision': t.bundle['research_loop']['revision'],
                      'material_sha256': state.catalog(t.bundle)['material_sha256']}
            reading['source_catalog'] = {'fixture_navigation': 'x' * 50000}
            before = copy.deepcopy(reading)
            data = json.loads(source_review.messages(t, reading, choice)[1]['content'])
            for key in ('evidence', 'source_context', 'reading_cursor', 'excluded_sources', 'future_extension'):
                self.assertEqual(data['reading'][key], before[key])
            self.assertEqual(reading, before)
            self.assertNotIn('source_catalog', data['reading'])
            self.assertTrue(data['reading_projection']['original_spans_unchanged'])
            self.assertIn('source_catalog', {r['field'] for r in data['reading_projection']['omitted_global_metadata']})

    def test_native_partial_acceptance_is_not_reported_as_a_source_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = self.fixture(tmp); seen = []
            def mutate(args, data, index): args['material_reviews'][0]['evidence_ids'] = []
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'fixture'}), patch.object(
                    post, 'ask_model', side_effect=self.model(seen, ['irrelevant'], mutate)):
                _, report = post.run(t.bundle, Path(tmp)/'post', http_cap=1)
            step = report['steps'][0]
            self.assertTrue(step['proposal_executed']); self.assertFalse(step['accepted'])
            self.assertEqual(step['status'], 'source_receipt_rejected')
            self.assertTrue(step['material_still_pending'])
            self.assertEqual(report['material_processing']['pending_material_count'], 2)

    def test_native_identity_covers_the_new_final_stage_dependency(self):
        from ForecastAgent.channels.contracts import code_identity
        self.assertIn('research_loop/source_review.py', code_identity())
        self.assertIn('research_loop/reference_map.py', code_identity())
        self.assertIn('research_loop/predictive_focus.py', code_identity())
