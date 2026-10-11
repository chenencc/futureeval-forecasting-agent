"""Local delivery failures cannot consume provider quota or corrupt originals."""
import copy
import json
import unittest

from ForecastAgent.research_loop.delivery import fit_messages, compact_navigation
from ForecastAgent.runtime.context import encode, ContextProjectionError


class DeliveryBudgetTests(unittest.TestCase):
    def messages(self, payload, extra=''):
        return [{'role': 'system', 'content': 'Guide'},
                {'role': 'user', 'content': encode(payload)},
                {'role': 'assistant', 'tool_calls': [{'id': 'c', 'function':
                    {'name': 'update_research_state', 'arguments': extra}}]},
                {'role': 'tool', 'tool_call_id': 'c', 'content': '{}'}]

    def test_drop_complete_repeated_group_before_source_spans(self):
        payload = {'immutable_question': {'resolution_criteria': 'Exact rules'},
                   'research_binding_frame': {'inspected_evidence': [{'evidence_id': 'r1',
                    'text': 'Original quotation', 'start': 0, 'end': 18}]}}
        before = copy.deepcopy(payload)
        messages, note = fit_messages(self.messages(payload, 'x' * 6000), payload, 1800)
        self.assertEqual(len(messages), 2)
        self.assertTrue(note['omitted_tool_group'])
        self.assertEqual(json.loads(messages[1]['content'])['immutable_question'], payload['immutable_question'])
        self.assertEqual(payload, before)
        self.assertEqual(note['delivered_evidence_ids'], ['r1'])

    def test_omissions_are_whole_spans_not_shortened_quotes(self):
        spans = [{'evidence_id': f'r{i}', 'text': 'Exact source words. ' * 75,
                  'url': 'https://example.org/source', 'body_sha256': 'a' * 64,
                  'start': i * 1500, 'end': i * 1500 + 1425} for i in range(4)]
        payload = {'immutable_question': {'resolution_criteria': 'Exact rule' * 20},
                   'research_binding_frame': {'inspected_evidence': spans, 'source_context': [spans[0]]}}
        before = copy.deepcopy(payload)
        messages, note = fit_messages(self.messages(payload), payload, 3600)
        self.assertLessEqual(len(encode(messages)), 3600)
        frame = json.loads(messages[1]['content'])['research_binding_frame']
        self.assertGreaterEqual(len(frame['inspected_evidence']), 1)
        for span in frame['inspected_evidence']:
            self.assertEqual(span, spans[int(span['evidence_id'][1:])])
        self.assertTrue(note['omitted_evidence_ids'])
        self.assertEqual(note['deduplicated_context_ids'], ['r0'])
        self.assertEqual(payload, before)

    def test_unfit_immutable_rules_raise_local_typed_failure(self):
        payload = {'immutable_question': {'resolution_criteria': 'x' * 2000}}
        with self.assertRaises(ContextProjectionError):
            fit_messages(self.messages(payload), payload, 1000)

    def test_navigation_compaction_precedes_whole_span_paging(self):
        spans=[{'evidence_id':'r1','text':'Exact first original','url':'https://example.org/a'},
               {'evidence_id':'r2','text':'Exact second original','url':'https://example.org/b'}]
        payload={'immutable_question':{'resolution_criteria':'Complete rules'},
                 'current_nodes':[{'id':'n1','interpretation':'Retained interpretation'}],
                 'targets':[{'id':'F1'}], 'material_requests':[{'gap_id':'g1'}],
                 'saved_sources':[{'url':'https://example.org/'+'a'*5000}],
                 'research_binding_frame':{'inspected_evidence':spans}}
        before=copy.deepcopy(payload)
        messages,note=fit_messages(self.messages(payload),payload,2200)
        delivered=json.loads(messages[1]['content'])
        self.assertEqual(note['delivered_evidence_ids'],['r1','r2'])
        self.assertTrue(note['navigation_compacted'])
        self.assertFalse(note['omitted_evidence_ids'])
        for key in ('immutable_question','current_nodes','targets','material_requests'):
            self.assertEqual(delivered[key],before[key])
        self.assertEqual(payload,before)

    def test_feedback_compaction_retains_nonduplicate_semantics(self):
        payload={'last_map_feedback':{'instruction':'Correct rejected role',
                    'rejected':[{'node_id':'n1','error':'Bad role','entry_sha256':'audit hash'}]},
                 'research_binding_frame':{'gap_feedback':{
                    'gaps':[{'gap_id':'g1','reason':'Missing dated value'}],
                    'pending_materials':[{'material_id':'m1','url':'https://example.org',
                        'scope':'Partial document','scope_sha256':'scope hash',
                        'action_intent_gap_ids':['g1'],'bound_observation_node_ids':[],
                        'inspected_reference_ids':['r1'],'origin':'native_action','acquisition_action_ids':[1]}],
                    'last_update':{'actual_graph_delta':{'added_node_ids':['n1']},
                        'program_explanation':{'node_changes':{'added_node_ids':['n1']},
                            'rejected_material_review_count':1,'rejected_proposed_node_ids':['n2'],
                            'future_semantic_field':'Must remain'}}}}}
        before=copy.deepcopy(payload)
        compact_navigation(payload)
        self.assertEqual(payload['last_map_feedback']['instruction'],'Correct rejected role')
        self.assertEqual(payload['last_map_feedback']['rejected'],[{'node_id':'n1','error':'Bad role'}])
        feedback=payload['research_binding_frame']['gap_feedback']
        self.assertEqual(feedback['gaps'],before['research_binding_frame']['gap_feedback']['gaps'])
        row=feedback['pending_materials'][0]
        self.assertEqual(row['scope'],'Partial document')
        self.assertEqual(row['action_intent_gap_ids'],['g1'])
        self.assertEqual(row['bound_observation_node_ids'],[])
        explanation=feedback['last_update']['program_explanation']
        self.assertEqual(explanation['future_semantic_field'],'Must remain')
        self.assertEqual(explanation['rejected_material_review_count'],1)
        self.assertEqual(explanation['rejected_proposed_node_ids'],['n2'])

