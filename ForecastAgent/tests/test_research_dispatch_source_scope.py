"""Observed linked exports and scheduled reviews retain exact native identity."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import dispatch, gap_feedback as gf, runtime, state
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.tests.test_research_dispatch import fresh
from ForecastAgent.tests.test_research_gap_feedback import initial, receipt, patch as proposal


def emitted_proposal(t, reviews):
    return {k: v for k, v in proposal(t, reviews).items()
            if k not in {'expected_revision', 'material_sha256'}}


class SourceScopeTests(unittest.TestCase):
    def add_link(self, t):
        parent = 'https://example.org/report'
        export = 'https://data.example.net/opaque/pub?output=csv'
        t.bundle['pages'][parent]['links'] = [export]
        t.bundle['source_leads'][parent] = {'origin': 'question_resolution_criteria', 'url': parent}
        t.bundle['source_leads'][export] = {'origin': 'page_link', 'url': export, 'parent_url': parent}
        return parent, export

    def test_literal_export_enters_pool_ahead_of_keyword_navigation(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); parent, export = self.add_link(t)
            t.bundle['searches'] = [{'results': [{'url': f'https://example.org/target-{i}',
                'title': 'Target release revenue'} for i in range(30)]}]
            before = copy.deepcopy(t.bundle); budget = t.budget()
            candidates = dispatch.discovered_candidates(t)
            self.assertEqual(candidates['urls'][0], export)
            proof = candidates['sources'][0]['link_provenance']
            self.assertEqual(proof['parent_url'], parent)
            self.assertEqual(proof['source_role'], 'linked_from_rule_source')
            self.assertTrue(proof['data_link']); self.assertFalse(proof['relevance_verified'])
            tools = [{'function': {'name': 'read_sources', 'parameters': {'type': 'object',
                'properties': {'urls': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string'}}},
                'required': ['urls']}}}]
            choice = {'phase': 'capture_frontier', 'tool': 'read_sources', 'candidates': candidates}
            routed = dispatch.tools_for(t, tools, choice)
            args, native, binding = dispatch.bind_arguments(t, 'read_sources',
                {'source_ids': [candidates['sources'][0]['source_id']]}, routed, choice)
            self.assertEqual(args, {'urls': [export]})
            self.assertFalse(binding['url_rewriting'])
            self.assertEqual(t.bundle, before); self.assertEqual(t.budget(), budget)

    def test_orphan_unobserved_unsafe_attempted_and_historical_leads_stay_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); parent, export = self.add_link(t)
            for u in ('https://example.org/orphan', 'https://example.org/invented', 'http://127.0.0.1/private'):
                t.bundle['source_leads'][u] = {'url': u, 'origin': 'page_link',
                    'parent_url': 'https://absent.example/' if 'orphan' in u else parent}
            t.bundle['pages'][parent]['links'].append('http://127.0.0.1/private')
            pool = dispatch.discovered_candidates(t)
            self.assertEqual(pool['urls'], [export])
            t.bundle['fetch_attempts'] = [{'url': export, 'status': 'failed'}]
            self.assertEqual(dispatch.discovered_candidates(t)['urls'], [])
            t.bundle['fetch_attempts'] = []; t.cutoff = '2026-10-01'
            self.assertEqual(dispatch.discovered_candidates(t)['urls'], [])

    def test_packed_registry_keeps_parent_link_proof(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); self.add_link(t)
            candidates = dispatch.discovered_candidates(t)
            choice = {'phase': 'capture_frontier', 'candidates': candidates}
            attempts = []
            def context(task, **kwargs):
                # Force the compact registry tier while retaining all proof.
                from ForecastAgent.runtime.context import ContextProjectionError
                attempts.append(kwargs['max_chars'])
                if len(attempts) < 4:
                    raise ContextProjectionError('Fixture full reading')
                return [{'role': 'user', 'content': 'x' * 26000}]
            with patch('ForecastAgent.runtime.context.collection_context', context):
                packet = json.loads(dispatch.model_context(t, choice)[-1]['content'])['research_dispatch']['candidates']
            if 'source_rows' in packet:
                visible = dict(zip(packet['source_columns'], packet['source_rows'][0]))
            else:
                visible = packet['sources'][0]
            self.assertEqual(visible['link_provenance'], candidates['sources'][0]['link_provenance'])
            self.assertIn('source_rows', packet)

    def test_first_tracking_parent_does_not_hide_a_literal_rule_parent(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root); parent, export = self.add_link(t)
            alias = parent + '?tracking=original'
            t.bundle['pages'][alias] = copy.deepcopy(t.bundle['pages'][parent])
            t.bundle['source_leads'][export]['parent_url'] = alias
            before = copy.deepcopy(t.bundle)
            proof = dispatch.linked_sources(t)[export]
            self.assertEqual(proof['parent_url'], parent)
            self.assertEqual(proof['source_role'], 'linked_from_rule_source')
            self.assertEqual(t.bundle, before)
            # Identical body alone does not prove that a URL was linked there.
            t.bundle['pages'][parent]['links'] = []
            proof = dispatch.linked_sources(t)[export]
            self.assertEqual(proof['parent_url'], alias)
            self.assertEqual(proof['source_role'], 'observed_page_link')

    def ready(self, root):
        t = fresh(root); initial(t)
        url = 'https://example.org/another'
        t.bundle['pages'][url] = {'content': 'This reports a different subgroup; the target remains unknown. ' * 10}
        choice = dispatch.choose(t)
        self.assertEqual(choice['phase'], 'process_read')
        dispatch.record_selection(t, choice)
        t.execute(choice['tool'], {'url': choice['url'], 'limit': 3}, 'offline')
        choice = dispatch.choose(t)
        self.assertEqual(choice['phase'], 'process_update')
        tools = runtime.configure(t, copy.deepcopy(COLLECTION_TOOLS))
        return t, choice, tools

    def test_wrong_material_and_foreign_reference_fail_before_native_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, tools = self.ready(root)
            valid = receipt(t, choice['url'], 'irrelevant', [], 'no_change')
            before = copy.deepcopy(t.bundle); budget = t.budget()
            wrong = copy.deepcopy(valid); wrong['material_id'] = 'invented'
            foreign = copy.deepcopy(valid)
            foreign['evidence_ids'] = receipt(t, 'https://example.org/report')['evidence_ids']
            for reviews in ([], [wrong], [foreign], [valid, valid]):
                with self.assertRaises(ValueError):
                    dispatch.bind_arguments(t, 'update_research_state', emitted_proposal(t, reviews), tools, choice)
                self.assertEqual(t.bundle, before); self.assertEqual(t.budget(), budget)

    def test_valid_no_edit_receipt_acknowledges_only_scheduled_source(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, tools = self.ready(root)
            old_nodes = copy.deepcopy(t.bundle['research_loop']['current']['nodes']); budget = t.budget()
            review = receipt(t, choice['url'], 'irrelevant', [], 'no_change')
            args = emitted_proposal(t, [review])
            bound, _, proof = dispatch.bind_arguments(t, 'update_research_state', args, tools, choice)
            self.assertEqual(args, {k: v for k, v in bound.items()
                                   if k not in proof['program_bound_fields']})
            self.assertFalse(proof['factual_arguments_rewritten'])
            result = t.execute('update_research_state', bound, 'offline')
            self.assertTrue(result['material_acknowledged']); self.assertFalse(result['committed'])
            self.assertEqual(t.bundle['research_loop']['current']['nodes'], old_nodes)
            self.assertEqual(t.budget(), budget)

    def test_deferred_stays_pending_and_stale_scope_is_not_rebound(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, tools = self.ready(root)
            review = receipt(t, choice['url'], 'deferred', [], 'unknown'); review['evidence_ids'] = []
            args, _, _ = dispatch.bind_arguments(t, 'update_research_state', emitted_proposal(t, [review]), tools, choice)
            t.execute('update_research_state', args, 'offline')
            self.assertIn(choice['material_id'], [m['material_id'] for m in gf.pending(t)])
            t.bundle['pages'][choice['url']]['content'] += ' New body version.'
            with self.assertRaisesRegex(ValueError, 'current material scope'):
                dispatch.bind_arguments(t, 'update_research_state', args, tools, choice)

    def test_model_cannot_override_program_revision_or_body_hash(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, tools = self.ready(root)
            args = emitted_proposal(t, [receipt(t, choice['url'], 'deferred', [], 'unknown')])
            before = copy.deepcopy(t.bundle)
            for field, value in (('expected_revision', choice['revision'] + 1),
                                 ('material_sha256', '0' * 64)):
                with self.assertRaisesRegex(ValueError, 'program-owned'):
                    dispatch.bind_arguments(t, 'update_research_state', {**args, field: value}, tools, choice)
            self.assertEqual(t.bundle, before)
            routed = dispatch.tools_for(t, tools, choice)
            schema = next(t['function']['parameters'] for t in routed
                          if t['function']['name'] == 'update_research_state')
            self.assertNotIn('expected_revision', schema['properties'])
            self.assertNotIn('material_sha256', schema['properties'])

    def test_current_source_cannot_replace_or_retire_foreign_observation(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, tools = self.ready(root)
            args = emitted_proposal(t, [receipt(t, choice['url'], 'deferred', [], 'unknown')])
            old_node = t.bundle['research_loop']['current']['nodes'][0]
            from ForecastAgent.research_loop.delta import node_input
            wrong = node_input(old_node)
            wrong['evidence_ids'] = dispatch.review_scope(t, choice)['inspected_reference_ids'][:3]
            before = copy.deepcopy(t.bundle)
            with self.assertRaisesRegex(ValueError, 'cannot rebind'):
                dispatch.bind_arguments(t, 'update_research_state', {**args, 'nodes': [wrong]}, tools, choice)
            with self.assertRaisesRegex(ValueError, 'cannot retire'):
                dispatch.bind_arguments(t, 'update_research_state', {**args,
                    'retired_node_ids': [old_node['id']]}, tools, choice)
            self.assertEqual(t.bundle, before)
            self.assertIn(old_node['id'], dispatch.review_scope(t, choice)['protected_observation_node_ids'])

    def test_unchanged_foreign_node_may_be_retained_without_rebinding(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, tools = self.ready(root)
            from ForecastAgent.research_loop.delta import node_input
            args = emitted_proposal(t, [receipt(t, choice['url'], 'deferred', [], 'unknown')])
            old_node = node_input(t.bundle['research_loop']['current']['nodes'][0])
            bound, _, proof = dispatch.bind_arguments(t, 'update_research_state',
                {**args, 'nodes': [old_node]}, tools, choice)
            self.assertEqual(bound['nodes'], [old_node])
            self.assertFalse(proof['factual_arguments_rewritten'])

    def test_model_receives_exact_review_scope_without_budget_or_graph_changes(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, tools = self.ready(root)
            before = copy.deepcopy(t.bundle); budget = t.budget()
            with patch('ForecastAgent.runtime.context.collection_context',
                       return_value=[{'role': 'user', 'content': 'exact saved original'}]):
                message = dispatch.model_context(t, choice)[-1]
            visible = json.loads(message['content'])['research_dispatch']
            expected = gf.materials(t)[choice['material_id']]
            for key in ('material_id', 'url', 'body_sha256', 'inspected_reference_ids'):
                self.assertEqual(visible['review_scope'][key], expected[key])
            self.assertEqual(choice, {k: v for k, v in visible.items()
                if k not in {'review_scope', 'instruction'}} | {'instruction': choice['instruction']})
            self.assertEqual(t.bundle['research_loop'], before['research_loop'])
            self.assertEqual(t.budget(), budget)


if __name__ == '__main__':
    unittest.main()
