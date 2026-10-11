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
    def test_context_projection_preserves_registry_and_objective_without_new_calls(self):
        from ForecastAgent.runtime.context import ContextProjectionError
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            choice = {'phase': 'capture_discovery', 'candidates': {'urls': ['https://example.org/a'],
                'sources': [{'source_id': 'L1', 'url': 'https://example.org/a', 'title': 'Exact metric',
                    'published_date': '2026-10-10', 'discovery_excerpt': 'x' * 600}]}}
            original = copy.deepcopy(choice)
            budget = copy.deepcopy(t.budget())
            calls = []
            def project(task, *, forced_tool, max_chars):
                calls.append(max_chars)
                if len(calls) == 1:
                    raise ContextProjectionError('Saved objective needs more room')
                return [{'role': 'user', 'content': task.bundle['request']['question']}]
            with patch('ForecastAgent.runtime.context.collection_context', side_effect=project):
                messages = dispatch.model_context(t, choice)
            menu = json.loads(messages[-1]['content'])['research_dispatch']['candidates']
            self.assertNotIn('urls', menu)
            self.assertEqual(menu['sources'][0]['source_id'], 'L1')
            self.assertEqual(menu['sources'][0]['url'], 'https://example.org/a')
            self.assertEqual(menu['sources'][0]['title'], 'Exact metric')
            self.assertEqual(len(menu['sources'][0]['discovery_excerpt']), 300)
            self.assertEqual(menu['sources'][0]['excerpt_omitted_chars'], 300)
            self.assertGreater(calls[1], calls[0])
            self.assertEqual(messages[0]['content'], t.bundle['request']['question'])
            self.assertEqual(choice, original)
            self.assertEqual(t.budget(), budget)
            self.assertEqual(t.bundle['dispatch_context_projections'][-1]['provider_calls'], 0)

    def test_context_projection_retains_explicit_failure_when_headers_do_not_fit(self):
        from ForecastAgent.runtime.context import ContextProjectionError
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            choice = {'phase': 'capture_frontier', 'candidates': {'urls': [], 'sources': []}}
            with patch('ForecastAgent.runtime.context.collection_context',
                       side_effect=ContextProjectionError('Immutable objective cannot fit')) as mocked:
                with self.assertRaises(ContextProjectionError):
                    dispatch.model_context(t, choice)
            self.assertEqual(mocked.call_count, 3)
            self.assertNotIn('dispatch_context_projections', t.bundle)

    def test_discovery_pool_is_not_truncated_to_capture_batch_size(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            exact = 'https://example.org/2026-10-10/detalle'
            hits = [{'url': f'https://example.org/older-{i}', 'title': 'Target release'} for i in range(5)]
            hits.append({'url': exact, 'title': 'Situacion actual', 'published_date': '2026-10-10',
                         'content': 'Latest dated status for the named location.'})
            t.bundle['searches'] = [{'results': hits}]
            before = copy.deepcopy(t.bundle); budget = copy.deepcopy(t.budget())
            choice = dispatch.choose(t)
            self.assertIn(exact, choice['candidates']['urls'])
            self.assertEqual(len(choice['candidates']['urls']), 6)
            source = next(s for s in choice['candidates']['sources'] if s['url'] == exact)
            self.assertEqual(source['evidence_status'], 'unread_discovery_lead')
            self.assertIn('named location', source['discovery_excerpt'])
            tools = [{'function': {'name': 'read_sources', 'parameters': {'properties':
                {'urls': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string'}}}}}}]
            routed = dispatch.tools_for(t, tools, choice)
            selection = routed[0]['function']['parameters']['properties']['source_ids']
            self.assertEqual(selection['maxItems'], 4)
            source_id = source['source_id']
            self.assertIn(source_id, selection['items']['enum'])
            args, native, binding = dispatch.bind_arguments(t, 'read_sources',
                {'source_ids': [source_id]}, routed, choice)
            self.assertEqual(args, {'urls': [exact]})
            self.assertEqual(binding['urls'], [exact])
            self.assertFalse(binding['url_rewriting'])
            self.assertEqual(native[0]['function']['parameters']['properties']['urls']['maxItems'], 4)
            self.assertIn('source_ids', routed[0]['function']['parameters']['properties'])
            for invalid in ({'source_ids': ['invented']}, {'source_ids': [source_id, source_id]},
                            {'source_ids': [source_id], 'urls': [exact]}):
                with self.assertRaises(ValueError):
                    dispatch.bind_arguments(t, 'read_sources', invalid, routed, choice)
            # Initialization may add a ledger; observed data and quota remain unchanged.
            self.assertEqual(t.bundle['searches'], before['searches'])
            self.assertEqual(t.budget(), budget)

    def test_candidate_pool_excludes_attempted_aliases_navigation_and_saved_pages(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            t.bundle['searches'] = [{'results': [{'url': f'https://example.org/detail-{i}',
                                                  'title': 'Target'} for i in range(30)]}]
            t.bundle['fetch_attempts'] = [{'url': 'https://example.org/detail-0/', 'status': 'failed'}]
            t.bundle['source_leads']['https://example.org/navigation'] = {
                'url': 'https://example.org/navigation', 'origin': 'page_link'}
            candidates = dispatch.discovered_candidates(t)
            self.assertEqual(len(candidates['urls']), 24)
            self.assertNotIn('https://example.org/detail-0', candidates['urls'])
            self.assertNotIn('https://example.org/navigation', candidates['urls'])
            self.assertFalse(set(candidates['urls']) & set(t.bundle['pages']))
            t.cutoff = '2026-10-01'
            self.assertEqual(dispatch.discovered_candidates(t)['urls'], [])

    def test_committed_review_yields_to_unread_frontier_without_claiming_pending_processed(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); initial(t)
            t.bundle['pages']['https://example.org/second'] = {
                'content':'An additional saved background source still needs source-bound review.'}
            url='https://example.org/target-current-detail'
            t.bundle['searches']=[{'results':[{'url':url,'title':'Target current detail'}]}]
            t.bundle['control']['read_after_discovery']=1
            dispatch.ledger(t)['selections'].append({'phase':'process_update','revision':0})
            before=copy.deepcopy(t.bundle['pages']);budget=copy.deepcopy(t.budget())
            pending=dispatch.status(t)['pending_material_count']
            choice=dispatch.choose(t)
            self.assertEqual(choice['phase'],'capture_frontier')
            self.assertEqual(choice['candidates']['urls'],[url])
            self.assertEqual(t.bundle['pages'],before)
            self.assertEqual(t.budget(),budget)
            self.assertEqual(dispatch.status(t)['pending_material_count'],pending)
            for _ in range(2):dispatch.record_selection(t,choice)
            self.assertNotEqual(dispatch.choose(t)['phase'],'capture_frontier')
            # Failed/attempted aliases must not become a fresh capture slot.
            dispatch.ledger(t)['selections']=dispatch.ledger(t)['selections'][:1]
            t.bundle['fetch_attempts']=[{'url':url+'/','status':'failed'}]
            self.assertNotEqual(dispatch.choose(t)['phase'],'capture_frontier')

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
            tools = [{'function': {'name': name, 'parameters': {'properties': {}}}} for name in
                     ['fetch_page', 'search_tavily', 'load_research_skill', 'inspect_research_state']]
            self.assertEqual([x['function']['name'] for x in dispatch.tools_for(t, tools, choice)],
                             ['fetch_page', 'search_tavily'])
            dispatch.record_selection(t, choice)
            self.assertEqual(dispatch.choose(t)['phase'], 'acquire_gap')
            dispatch.record_selection(t, choice)
            self.assertIsNone(dispatch.choose(t))
            t.bundle['control']['forced_close'] = True
            self.assertIsNone(dispatch.choose(t))

    def test_future_realization_does_not_force_network(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); initial(t)
            self.assertIsNone(dispatch.choose(t))
            self.assertEqual(t.budget()['tavily_basic_remaining'], 3)

    def test_page_intent_maps_to_real_capture_tool_and_requires_gap_link(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); initial(t)
            p = proposal(t, [])
            p['material_requests'] = copy.deepcopy(t.bundle['research_loop']['current']['material_requests'])
            p['material_requests'][0].update(availability='available', suggested_tool='page_fetch')
            p['revision_kind'] = 'interpretation_correction'
            t.execute('update_research_state', p, 'test')
            choice = dispatch.choose(t)
            self.assertEqual(choice['tool'], 'read_sources')
            from ForecastAgent.runtime.tool_selection import active_tools
            from ForecastAgent.tools.registry import COLLECTION_TOOLS
            from ForecastAgent.research_loop import runtime
            tools = active_tools(t, runtime.configure(t, copy.deepcopy(COLLECTION_TOOLS)), choice['tool'])
            tools = dispatch.tools_for(t, tools, choice)
            self.assertEqual([x['function']['name'] for x in tools], ['read_sources'])
            self.assertIn('research_gap_ids', tools[0]['function']['parameters']['required'])
            urls = tools[0]['function']['parameters']['properties']['urls']['items']['enum']
            self.assertIn('https://example.org/report', urls)
            self.assertNotIn('https://example.org/invented', urls)

    def test_initial_discovery_captures_before_empty_map_or_skill_navigation(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); t.bundle['pages'] = {}
            t.bundle['searches'] = [{'results': [{'url': 'https://example.org/report', 'title': 'Target release'}]}]
            choice = dispatch.choose(t)
            self.assertEqual(choice['phase'], 'capture_discovery')
            self.assertEqual(choice['tool'], 'read_sources')

    def test_new_discovery_is_read_before_reinterpreting_an_existing_page(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            original = copy.deepcopy(t.bundle['pages'])
            url = 'https://example.org/current-statistics'
            t.bundle['searches'] = [{'results': [{'url': url, 'title': 'Target latest official data'}]}]
            budget = copy.deepcopy(t.budget())
            choice = dispatch.choose(t)
            self.assertEqual(choice['phase'], 'capture_discovery')
            self.assertIn(url, choice['candidates']['urls'])
            tools = [{'function': {'name': 'read_sources', 'parameters': {'properties':
                {'urls': {'type':'array','items':{'type':'string'}}}}}}]
            selected = dispatch.tools_for(t, tools, choice)
            source_id = choice['candidates']['sources'][0]['source_id']
            self.assertEqual(selected[0]['function']['parameters']['properties']['source_ids']['items']['enum'], [source_id])
            self.assertEqual(t.bundle['pages'], original)
            self.assertEqual(t.budget(), budget)
            # Invalid proposals cannot create an endless forced-reading loop.
            dispatch.record_selection(t, choice)
            dispatch.record_selection(t, dispatch.choose(t))
            self.assertEqual(dispatch.choose(t)['phase'], 'process_read')
            # A completed reading consumes the existing obligation, not new quota.
            t.bundle['control']['read_after_discovery'] = 1
            self.assertEqual(dispatch.choose(t)['phase'], 'process_read')

    def test_discovery_priority_cannot_override_hard_close_or_fetch_cap(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            t.bundle['searches'] = [{'results': [{'url': 'https://example.org/detail', 'title': 'Target'}]}]
            t.bundle['control']['forced_close'] = True
            self.assertIsNone(dispatch.choose(t))
            t.bundle['control']['forced_close'] = False
            budget = t.budget(); budget['page_fetch_remaining'] = 0
            with patch.object(t, 'budget', return_value=budget):
                self.assertEqual(dispatch.choose(t)['phase'], 'process_read')

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
