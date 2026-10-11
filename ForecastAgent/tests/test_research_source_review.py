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
