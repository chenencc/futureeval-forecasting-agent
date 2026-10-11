"""Whole stale-reading deferral and execution/forecast contract isolation."""
import copy
import json
import unittest
from ForecastAgent.research_loop.delivery import fit_messages
from ForecastAgent.research_loop.scoring_input import project
from ForecastAgent.runtime.context import ContextProjectionError, encode


class PhaseBoundaryTests(unittest.TestCase):
    def payload(self):
        return {'immutable_question': {'question': 'Exact target', 'resolution_criteria': 'Exact rule'},
            'research_binding_frame': {'inspected_evidence': [{'evidence_id': 'R1', 'text': 'exact ' * 300}],
                'source_context': [], 'material_sha256': 'unchanged', 'pending_map_update': False}}

    def test_source_capture_defers_whole_saved_reading_without_false_delivery(self):
        payload = self.payload()
        payload['reading_scope'] = 'deferred_during_source_capture'
        parent = copy.deepcopy(payload)
        messages = [{'role': 'system', 'content': 'Capture only'}, {'role': 'user', 'content': ''}]
        delivered, receipt = fit_messages(messages, payload, 1500)
        value = json.loads(delivered[1]['content'])
        self.assertEqual(payload, parent)
        self.assertEqual(value['immutable_question'], payload['immutable_question'])
        self.assertEqual(value['research_binding_frame']['material_sha256'], 'unchanged')
        self.assertEqual(receipt['delivered_evidence_ids'], [])
        self.assertEqual(receipt['deferred_reading_ids'], ['R1'])
        self.assertFalse(receipt['source_text_changed'])
        self.assertLessEqual(len(encode(delivered)), 1500)

    def test_map_update_and_unspecified_phases_cannot_defer_last_exact_reading(self):
        messages = [{'role': 'system', 'content': 'Bind originals'}, {'role': 'user', 'content': ''}]
        for scope in (None, 'map_update'):
            payload = self.payload()
            if scope:
                payload['reading_scope'] = scope
            with self.assertRaises(ContextProjectionError):
                fit_messages(messages, payload, 1300)

    def test_forecast_boundary_keeps_rules_scale_unknown_fields_and_all_receipts(self):
        native = {'request': {'id': '123', 'question': 'Question', 'resolution_criteria': 'Rule',
            'question_type': 'discrete', 'fine_print': 'Exception', 'background': 'Context',
            'options': ['a', 'b'], 'scaling': {'continuous_range': [1, 2]}, 'inbound_outcome_count': 1,
            'open_lower_bound': True, 'open_upper_bound': False, 'as_of_utc': 'clock',
            'future_platform_field': {'literal': 'keep'}, 'capability_code_identity': {'file': 'hash'},
            'research_dispatch_limits': {'calls': 3}, 'predictive_information_contract': {'clock': 'clock'}},
            'pages': {'url': {'content': 'Exact original'}}, 'model_attempts': [{'status': 'received'}]}
        parent = copy.deepcopy(native)
        view, audit = project(native)
        self.assertEqual(native, parent)
        for field in set(native['request']) - set(audit['omitted_execution_field_sha256']):
            self.assertEqual(view['request'][field], native['request'][field])
        self.assertEqual(view['pages'], native['pages'])
        self.assertEqual(view['model_attempts'], native['model_attempts'])
        self.assertNotIn('capability_code_identity', view['request'])
        self.assertEqual(audit['new_http'], 0)

    def test_incomplete_forecast_contract_rejected(self):
        with self.assertRaises(ValueError):
            project({'request': {'id': '123'}})


if __name__ == '__main__':
    unittest.main()
