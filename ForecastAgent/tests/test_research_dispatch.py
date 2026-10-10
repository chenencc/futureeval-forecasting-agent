"""Bounded graph-to-acquisition routing and pending-source processing gates."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import dispatch, gap_feedback as gf, fusion, state, post_supplement as post
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.tests.test_research_gap_feedback import task, initial, receipt, patch as proposal
from ForecastAgent.tests.test_post_supplement_map import PostSupplementTests


def fresh(root):
    template = task(Path(root) / 'template')
    request = {**template.bundle['request'], dispatch.FIELD: dispatch.POLICY,
               dispatch.LIMIT_FIELD: dict(dispatch.DEFAULTS), post.FIELD: post.POLICY}
    t = RetrievalTask(Path(root) / 'candidate', request)
    t.bundle['pages'] = copy.deepcopy(template.bundle['pages'])
    t.bundle['plan'] = copy.deepcopy(template.bundle['plan'])
    fusion.initialize(t)
    t.save()
    return t


class DispatchTests(unittest.TestCase):
    def test_frozen_caps_survive_restart_and_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            self.assertEqual(t.budget()['page_fetch_remaining'], 16)
            self.assertEqual(t.bundle['research_loop']['update_cap'], 10)
            t.save()
            restored = RetrievalTask(t.directory, t.bundle['request'])
            self.assertEqual(restored.budget(), t.budget())
            altered = copy.deepcopy(t.bundle)
            altered['request'][dispatch.LIMIT_FIELD]['map_updates'] = 11
            with self.assertRaisesRegex(ValueError, 'allowance changed'):
                state.initialize(altered)
            self.assertEqual(dispatch.limits({})['map_updates'], 3)

    def test_read_then_update_same_source_and_rejections_are_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            for _ in range(2):
                choice = dispatch.choose(t)
                self.assertEqual(choice['phase'], 'process_read')
                dispatch.record_selection(t, choice)
                t.execute(choice['tool'], {'url': choice['url'], 'limit': 3}, 'test')
                follow = dispatch.choose(t)
                self.assertEqual(follow['phase'], 'process_update')
                self.assertEqual(follow['material_id'], choice['material_id'])
                dispatch.record_selection(t, follow)
            self.assertIsNone(dispatch.choose(t))
            self.assertEqual(dispatch.status(t)['status'], 'processing_with_gaps')

    def test_committed_map_routes_to_obtainable_gap_without_skill_noise(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); initial(t)
            p = proposal(t, [])
            p['material_requests'] = copy.deepcopy(t.bundle['research_loop']['current']['material_requests'])
            p['material_requests'][0]['availability'] = 'available'
            p['revision_kind'] = 'interpretation_correction'
            t.execute('update_research_state', p, 'test')
            choice = dispatch.choose(t)
            self.assertEqual(choice['phase'], 'acquire_gap')
            tools = [{'function': {'name': name}} for name in
                     ['fetch_page', 'search_tavily', 'load_research_skill', 'inspect_research_state']]
            self.assertEqual([x['function']['name'] for x in dispatch.tools_for(t, tools, choice)],
                             ['fetch_page', 'search_tavily'])
            dispatch.record_selection(t, choice)
            self.assertIsNone(dispatch.choose(t))
            t.bundle['control']['forced_close'] = True
            self.assertIsNone(dispatch.choose(t))

    def test_future_realization_does_not_force_network(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); initial(t)
            self.assertIsNone(dispatch.choose(t))
            self.assertEqual(t.budget()['tavily_basic_remaining'], 3)

    def test_reserved_reviewer_continues_after_partial_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            t.bundle['request'][post.FIELD] = post.POLICY
            url = 'https://example.org/unrelated'
            t.bundle['pages'][url] = {'content': 'Different company and league, unrelated background. ' * 20}
            original = copy.deepcopy(t.bundle)
            control = PostSupplementTests(); control.original = original
            count = []
            def model(messages, api_key, **kwargs):
                count.append(1)
                if len(count) == 1:
                    return control.fake_model(messages, api_key, **kwargs)
                data = json.loads(messages[1]['content']); reading = data['reading']
                span = next(s for s in reading['evidence'] if s['url'] == url)
                args = {'expected_revision': data['expected_revision'],
                    'material_sha256': reading['material_sha256'],
                    'revision_kind': 'interpretation_correction', 'update_mode': 'merge',
                    'nodes': [], 'relations': [], 'material_requests': [], 'retired_node_ids': [],
                    'supporting_path': '', 'alternative_path': '', 'revision_reason': 'Disposed of unrelated source.',
                    'material_reviews': [{'material_id': span['material_id'], 'disposition': 'irrelevant',
                        'evidence_ids': [span['evidence_id']], 'node_ids': [], 'gap_ids': [],
                        'related_material_ids': [], 'effect': 'no_change', 'reason': 'Different league and company.'}]}
                observer = kwargs['observer']; token = observer('reserve', {'status': 'reserved'})
                observer('received', {'status': 'received', 'usage': {'total_tokens': 100}}, token)
                return {'tool_calls': [{'function': {'name': 'update_research_state', 'arguments': json.dumps(args)}}]}
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test'}), patch(
                    'ForecastAgent.research_loop.post_supplement.ask_model', side_effect=model):
                child, report = post.run(original, Path(root) / 'review', http_cap=6)
            self.assertEqual(len(count), 2)
            self.assertEqual(report['material_processing']['pending_material_count'], 0)
            self.assertEqual(report['status'], 'reviewed')
            for key in post.PRESERVED:
                self.assertEqual(child.get(key), original.get(key))
            self.assertEqual(digest(original), digest(t.bundle))


if __name__ == '__main__':
    unittest.main()
