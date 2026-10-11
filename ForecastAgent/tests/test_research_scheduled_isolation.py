"""Scheduled binding must reach native isolation without weakening receipts."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import dispatch, gap_feedback, reference_map, runtime, state, target_logic


class ScheduledIsolationTests(unittest.TestCase):
    def prepare(self, root):
        from ForecastAgent.tests.test_research_source_review import SourceReviewTests
        task = SourceReviewTests().fixture(root)
        task.bundle['request'].update({reference_map.FIELD: reference_map.POLICY,
                                      target_logic.FIELD: target_logic.POLICY})
        source = gap_feedback.pending(task)[0]
        task.execute('inspect_research_state', {'url': source['url'], 'limit': 3}, '')
        choice = {'phase': 'process_update', 'tool': 'update_research_state', 'url': source['url'],
                  'material_id': source['material_id'],
                  'revision': task.bundle['research_loop']['revision'],
                  'material_sha256': state.catalog(task.bundle)['material_sha256']}
        scope = dispatch.review_scope(task, choice)
        node = reference_map.input_node(task.bundle['research_loop']['current']['nodes'][0])
        node.update(id='new_source', kind='observation', hypothesis='',
                    evidence_ids=scope['inspected_reference_ids'][:1], interpretation='A saved report.',
                    limitation='Applicability is unverified.', applicability='unknown', target_links=[],
                    gap_reason='none')
        args = {'revision_kind': 'material_update', 'update_mode': 'merge',
                'nodes': [node], 'relations': [], 'material_requests': [], 'retired_node_ids': [],
                'supporting_path': '', 'alternative_path': '', 'revision_reason': 'Bind this source.',
                'material_reviews': [{'material_id': source['material_id'], 'disposition': 'incorporated',
                    'evidence_ids': node['evidence_ids'], 'node_ids': ['new_source'], 'gap_ids': [],
                    'related_material_ids': [], 'effect': 'no_change', 'reason': 'Retain the original.'}]}
        tools = runtime.configure(task, [])
        return task, choice, args, tools

    def run_update(self, task, choice, args, tools):
        bound, _, proof = dispatch.bind_arguments(task, 'update_research_state', args, tools, choice)
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network allowed')):
            outcome = runtime.execute(task, 'update_research_state', bound)
        return outcome, proof

    def test_optional_annotation_errors_keep_exact_original_with_rejections(self):
        for field, value in [('interpretation', 'x'*241), ('limitation', {'wrong': 'type'}),
                             ('target_links', [{'target_id': 'invented', 'role': 'bad',
                                                'effect': 'supports', 'reason': 'Unverified'}])]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                t, choice, args, tools = self.prepare(root)
                args['nodes'][0][field] = value
                submitted = copy.deepcopy(args); pages = copy.deepcopy(t.bundle['pages']); budget = t.budget()
                result, proof = self.run_update(t, choice, args, tools)
                node = next(n for n in t.bundle['research_loop']['current']['nodes'] if n['id']=='new_source')
                self.assertEqual(node['evidence_ids'], submitted['nodes'][0]['evidence_ids'])
                self.assertFalse(node['interpretation_verified'])
                self.assertTrue(result['acceptance']['reference_binding_receipts'][0]['annotation_rejections'])
                self.assertEqual(len(result['gap_feedback']['accepted_reviews']), 1)
                self.assertIn('nodes', proof['native_isolation_sections'])
                self.assertTrue(proof['scheduled_receipt_validated_atomically'])
                self.assertEqual(args, submitted); self.assertEqual(t.bundle['pages'], pages)
                self.assertEqual(t.budget(), budget)
                if field in {'interpretation', 'limitation'}:
                    self.assertEqual(node[field], ''); self.assertEqual(node['target_links'], [])

    def test_invalid_sibling_relation_and_need_are_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, args, tools = self.prepare(root)
            args['nodes'] += [7, {**args['nodes'][0], 'id': 'bad_reference', 'evidence_ids': ['R_invented']}]
            args['relations'] = [{'from_id': 'new_source', 'to_id': 'missing', 'kind': 'causal'}]
            args['material_requests'] = [{'target': 'Incomplete need'}]
            result, _ = self.run_update(t, choice, args, tools)
            self.assertIn('new_source', result['acceptance']['accepted_node_ids'])
            self.assertNotIn('bad_reference', result['acceptance']['accepted_node_ids'])
            self.assertTrue({'nodes', 'relations', 'material_requests'} <=
                            {r['section'] for r in result['acceptance']['rejected']})
            self.assertEqual(len(result['gap_feedback']['accepted_reviews']), 1)

    def test_rejected_replacement_cannot_credit_source_without_retained_binding(self):
        with tempfile.TemporaryDirectory() as root:
            t, choice, args, tools = self.prepare(root)
            args['nodes'][0]['evidence_ids'] = ['R_invented']
            result, _ = self.run_update(t, choice, args, tools)
            self.assertEqual(result['gap_feedback']['accepted_reviews'], [])
            self.assertIn(choice['material_id'], {m['material_id'] for m in gap_feedback.pending(t)})

    def test_receipts_and_frame_stay_atomic_before_any_edit(self):
        for mutation in ('receipt_reference', 'receipt_source', 'two_receipts', 'frame', 'cap', 'stale'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                t, choice, args, tools = self.prepare(root)
                if mutation=='receipt_reference': args['material_reviews'][0]['evidence_ids']=['R_invented']
                elif mutation=='receipt_source': args['material_reviews'][0]['material_id']='M_wrong'
                elif mutation=='two_receipts': args['material_reviews'] *= 2
                elif mutation=='frame': args['expected_revision']=choice['revision']
                elif mutation=='cap': args['nodes'] *= 13
                else: choice['revision'] -= 1
                before = digest(t.bundle)
                with self.assertRaises(ValueError): dispatch.bind_arguments(t, 'update_research_state', args, tools, choice)
                self.assertEqual(digest(t.bundle), before)

    def test_protected_observation_cannot_be_rebound_or_retired(self):
        for mutation in ('retire', 'rebind', 'malformed'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                t, choice, args, tools = self.prepare(root)
                old = t.bundle['research_loop']['current']['nodes'][0]
                if mutation=='retire': args['retired_node_ids']=[old['id']]
                else:
                    args['nodes'][0]['id']=old['id']
                    if mutation=='malformed': args['nodes'][0]['evidence_ids']=[{}]
                before = digest(t.bundle)
                with self.assertRaisesRegex(ValueError, 'another source'):
                    dispatch.bind_arguments(t, 'update_research_state', args, tools, choice)
                self.assertEqual(digest(t.bundle), before)

    def test_long_operational_reason_survives_without_rewriting_factual_arguments(self):
        with tempfile.TemporaryDirectory() as root:
            t,choice,args,tools=self.prepare(root)
            args['revision_reason']='Retain the exact source and document the operational review. '*12
            submitted=copy.deepcopy(args);pages=copy.deepcopy(t.bundle['pages']);budget=t.budget()
            result,proof=self.run_update(t,choice,args,tools)
            preserved=proof['separate_operational_explanation']
            self.assertEqual(preserved['model_text'],submitted['revision_reason'])
            self.assertEqual(preserved['model_text_sha256'],digest(submitted['revision_reason']))
            self.assertFalse(preserved['factual_claims_applied'])
            self.assertEqual(len(result['gap_feedback']['accepted_reviews']),1)
            self.assertEqual(args,submitted);self.assertEqual(t.bundle['pages'],pages);self.assertEqual(t.budget(),budget)
            self.assertFalse(proof['factual_arguments_rewritten'])

    def test_short_reason_is_unchanged_and_bad_types_or_oversize_still_fail(self):
        with tempfile.TemporaryDirectory() as root:
            t,choice,args,tools=self.prepare(root)
            bound,_,proof=dispatch.bind_arguments(t,'update_research_state',args,tools,choice)
            self.assertEqual(bound['revision_reason'],args['revision_reason'])
            self.assertIsNone(proof['separate_operational_explanation'])
            before=digest(t.bundle)
            for value in (None,17,' '*500,'x'*2001):
                bad={**args,'revision_reason':value}
                with self.subTest(value_type=type(value).__name__),self.assertRaises(ValueError):
                    dispatch.bind_arguments(t,'update_research_state',bad,tools,choice)
                self.assertEqual(digest(t.bundle),before)

    def test_long_reason_never_bypasses_receipt_frame_or_lifetime_caps(self):
        for mutation in ('receipt','frame','cap'):
            with tempfile.TemporaryDirectory() as root:
                t,choice,args,tools=self.prepare(root)
                args['revision_reason']='Explain the source review independently of facts. '*12
                if mutation=='receipt':args['material_reviews'][0]['evidence_ids']=['R_invented']
                elif mutation=='frame':args['expected_revision']=choice['revision']
                else:args['nodes']*=13
                before=digest(t.bundle)
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                    dispatch.bind_arguments(t,'update_research_state',args,tools,choice)
                self.assertEqual(digest(t.bundle),before)


if __name__ == '__main__':
    unittest.main()
