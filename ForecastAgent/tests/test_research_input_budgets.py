"""Declared delivery ceilings and equal original/map evidence coverage."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from ForecastAgent.research_loop import context_budget, dispatch, shared_map_input
from ForecastAgent.runtime.context import ContextProjectionError, encode


class ContextCeilingTests(unittest.TestCase):
    def test_registered_hard_ceiling_is_used_before_another_http_call(self):
        task = SimpleNamespace(bundle={'request': {context_budget.FIELD:
            {'preferred_chars': 28000, 'maximum_chars': 40000}}})
        choice = {'phase': 'process_update', 'tool': 'update_research_state'}
        attempts = []
        def project(task, *, forced_tool, max_chars):
            attempts.append(max_chars)
            if max_chars < 32000:
                raise ContextProjectionError('Whole reading plus rules requires 32000')
            return [{'role': 'user', 'content': 'exact ' * 5300}]
        with patch('ForecastAgent.runtime.context.collection_context', project):
            messages = dispatch.model_context(task, choice, forced_tool=choice['tool'])
        self.assertEqual(len(attempts), 2)
        self.assertLessEqual(len(encode(messages)), 40000)
        record = task.bundle['dispatch_context_projections'][-1]
        self.assertEqual(record['ceiling_chars'], 40000)
        self.assertEqual(record['preferred_ceiling_chars'], 28000)
        self.assertEqual(record['provider_calls'], 0)

    def test_old_task_budget_and_true_overflow_remain_strict(self):
        for request in ({}, {context_budget.FIELD: {'preferred_chars': 28000, 'maximum_chars': 40000}}):
            task = SimpleNamespace(bundle={'request': request})
            with patch('ForecastAgent.runtime.context.collection_context', side_effect=ContextProjectionError('immutable overflow')):
                with self.assertRaises(ContextProjectionError):
                    dispatch.model_context(task, {'phase': 'process_update'})
            self.assertNotIn('dispatch_context_projections', task.bundle)

    def test_malformed_context_allowances_rejected(self):
        for value in (True, {'preferred_chars': True, 'maximum_chars': 40000},
            {'preferred_chars': 40000, 'maximum_chars': 28000},
            {'preferred_chars': 28000, 'maximum_chars': 100001}, {'maximum_chars': 40000}):
            with self.assertRaises(ValueError):
                context_budget.ceilings({context_budget.FIELD: value})
        with self.assertRaises(ValueError):
            dispatch.limits({context_budget.FIELD: {'preferred_chars': 28000, 'maximum_chars': 40000}})

    def test_final_routing_uses_registered_limit_and_still_rejects_true_overflow(self):
        messages = [{'role': 'user', 'content': 'x' * 32000}]
        for request, accepted in (({}, False),
            ({dispatch.FIELD: dispatch.POLICY, context_budget.FIELD:
                {'preferred_chars': 28000, 'maximum_chars': 40000}}, True)):
            task = SimpleNamespace(bundle={'request': request})
            if accepted:
                dispatch.validate_delivery(task, messages)
                with self.assertRaises(ContextProjectionError):
                    dispatch.validate_delivery(task, messages + [{'role': 'user', 'content': 'x' * 10000}])
            else:
                with self.assertRaises(ContextProjectionError):
                    dispatch.validate_delivery(task, messages)

    def test_real_collection_send_boundary_honors_opt_in_ceiling(self):
        from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
        from ForecastAgent.tests.test_research_loop import source_bundle
        from ForecastAgent.tests.test_collection import PLAN
        for registered, size, sends in ((False, 32000, 0), (True, 32000, 1), (True, 41000, 0)):
            request = copy.deepcopy(source_bundle()['request'])
            request[dispatch.FIELD] = dispatch.POLICY
            if registered:
                request[context_budget.FIELD] = {'preferred_chars': 28000, 'maximum_chars': 40000}
            with tempfile.TemporaryDirectory() as root, patch.dict('os.environ', {'EXA_API_KEY': ''}):
                task = RetrievalTask(Path(root), request)
                task.execute('plan_evidence', PLAN, '')
                task.save()
                messages = [{'role': 'user', 'content': 'x' * size}]
                with patch.object(dispatch, 'choose', return_value=None), \
                    patch.object(dispatch, 'model_context', return_value=messages), \
                    patch('ForecastAgent.runtime.material_protocol.closure_ready', return_value=False), \
                    patch('ForecastAgent.runtime.collection_actions.raw_stop_reason', return_value=None), \
                    patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=RuntimeError('Test send boundary reached')) as send, \
                    patch('urllib.request.urlopen', side_effect=AssertionError('No network')):
                    result = run_retrieval(request, Path(root), '', '')
                self.assertEqual(send.call_count, sends)
                self.assertEqual(result['last_error'], 'RuntimeError' if sends else 'ContextProjectionError')
                self.assertFalse(result.get('model_attempts', []))
                self.assertFalse(result['searches'])


class SharedMapOriginalTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://example.org/current-source'
        self.body = 'A complete dated observation, followed by exact context.'
        self.sha = hashlib.sha256(self.body.encode()).hexdigest()
        self.source = {'source_id': 'S1', 'url': self.url, 'body_sha256': self.sha}
        self.bundle = {'pages': {self.url: {'content': self.body}}}
        self.old_span = {'evidence_id': 'E1', 'source_id': 'S1', 'url': self.url,
            'body_sha256': self.sha, 'start': 5, 'end': 12, 'text': self.body[5:12]}
        self.original = {'question': {'question': 'Exact target'}, 'sources': [self.source], 'evidence': [self.old_span]}
        self.binding = {'url': self.url, 'body_sha256': self.sha, 'start': 0, 'end': 20,
            'coordinate_space': 'saved_body'}

    def packet(self):
        return patch.object(shared_map_input, 'packet_for', return_value={'sources': [self.source]})

    def test_complete_binding_union_keeps_every_original_span(self):
        before = copy.deepcopy(self.original)
        with self.packet():
            result, added = shared_map_input.bind_group(self.bundle, self.original, [self.binding])
        self.assertEqual(self.original, before)
        self.assertEqual(result['evidence'][0], self.old_span)
        self.assertEqual(len(added), 2)
        for span in result['evidence']:
            self.assertEqual(span['text'], self.body[span['start']:span['end']])
        self.assertEqual(sorted((s['start'], s['end']) for s in result['evidence']), [(0, 5), (5, 12), (12, 20)])

    def test_unavailable_view_hash_or_coordinates_cannot_be_fabricated(self):
        for delta in ({'body_sha256': 'different'}, {'view_sha256': 'decoded'},
            {'coordinate_space': 'parsed_view'}, {'start': True}, {'end': len(self.body) + 1}):
            with self.packet(), self.assertRaises(ValueError):
                shared_map_input.bind_group(self.bundle, self.original, [{**self.binding, **delta}])

    def test_both_arms_get_same_added_text_and_reserved_map_space(self):
        mapped = {'research_loop': {'current': {'nodes': [{'id': 'n1', 'kind': 'observation', 'bindings': [self.binding]}]}}}
        note = {'factual_grounding_present': True, 'nodes': [{'id': 'n1', 'interpretation': 'Unverified hypothesis'}]}
        with self.packet(), patch.object(shared_map_input.decision, 'audit', return_value={'usable': True}), \
                patch.object(shared_map_input.decision, 'scoring_map', return_value=(note, {})):
            common, enriched, report = shared_map_input.align(self.bundle, mapped, self.original, {'event': {}}, maximum=2000)
        self.assertTrue(report['independent_input'])
        self.assertEqual({k: v for k, v in enriched.items() if k != 'research_map'}, common)
        self.assertEqual(common['evidence'][0], self.old_span)
        self.assertGreater(report['reserved_map_room_bytes'], 64)
        self.assertLessEqual(report['mapped_bytes'] + 64, 2000)

    def test_no_room_preserves_input_and_does_not_claim_a_pair(self):
        mapped = {'research_loop': {'current': {'nodes': [{'id': 'n1', 'kind': 'observation', 'bindings': [self.binding]}]}}}
        # A model-facing map must not crowd out the protected originals.
        def notes(bundle, original):
            if len(original['evidence']) == 1:
                return {'factual_grounding_present': False}, {}
            return {'factual_grounding_present': True, 'nodes': [{'id': 'n1', 'claim': 'x' * 3000}]}, {}
        with self.packet(), patch.object(shared_map_input.decision, 'audit', return_value={'usable': True}), \
                patch.object(shared_map_input.decision, 'scoring_map', side_effect=notes):
            common, enriched, report = shared_map_input.align(self.bundle, mapped, self.original, {'event': {}}, maximum=2000)
        self.assertEqual(common, self.original)
        self.assertEqual(enriched, common)
        self.assertFalse(report['independent_input'])
        self.assertEqual(report['groups'][0]['status'], 'no_room')


if __name__ == '__main__':
    unittest.main()
