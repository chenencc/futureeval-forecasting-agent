"""Rule-ID integrity, negative evidence and explicit business failure gates."""
import copy
import json
import unittest
from ForecastAgent.supplement import acquisition_ids as ids
from ForecastAgent.supplement import acquisition_contract as base


class AcquisitionIDs(unittest.TestCase):
    def fixture(self):
        request = {'question': 'Was the permit issued?',
            'resolution_criteria': 'Permit issued before 2026-08-01. According to https://agency.example/status.\nNot revoked before the deadline.'}
        catalog = ids.rule_catalog(request)
        rule = next(r for r in catalog if r['field'] == 'resolution_criteria')
        need = {'id': 'stage', 'condition': 'Permit issuance stage', 'critical': True,
                'dimension': 'stage', 'target': 'issued', 'rule_ids': [rule['rule_id']]}
        bundle = {'request': request, 'pages': {'https://agency.example/status': {
            'content': 'The permit application remains pending; no issuance recorded.\n' * 12}}}
        return bundle, need

    def test_catalog_preserves_exact_text_including_unicode(self):
        request = {'question': 'Will it happen?\n', 'resolution_criteria': 'First clause. Second clause.\n数值 ≥ 5。'}
        catalog = ids.rule_catalog(request)
        for field, body in request.items():
            rows = [r for r in catalog if r['field'] == field]
            self.assertEqual(''.join(r['text'] for r in rows), body)
            for r in rows:
                self.assertEqual(body[r['start']:r['end']], r['text'])

    def test_rule_change_invalidates_old_ids(self):
        b, need = self.fixture()
        b['request']['resolution_criteria'] = b['request']['resolution_criteria'].replace('2026-08-01', '2026-09-01')
        plan = ids.bind_needs(b['request'], [need])
        self.assertFalse(plan['needs'])
        self.assertEqual(plan['rejected_needs'][0]['reason'], 'unknown_or_duplicate_rule_id')

    def test_rule_origins_are_program_owned(self):
        b, need = self.fixture(); plan = ids.bind_needs(b['request'], [need])
        self.assertEqual(len(plan['needs']), 1)
        self.assertFalse(plan['needs'][0]['target_semantics_verified'])
        self.assertNotIn('start', ids.tools()[0]['function']['parameters']['properties']['needs']['items']['properties'])

    def test_invalid_rule_record_is_isolated(self):
        b, need = self.fixture(); bad = {**need, 'id': 'bad', 'rule_ids': ['invented']}
        plan = ids.bind_needs(b['request'], [bad, need])
        self.assertEqual([n['id'] for n in plan['needs']], ['stage'])
        self.assertEqual(len(plan['rejected_needs']), 1)

    def test_complete_serialized_array_only(self):
        value, repairs = ids.envelope({'needs': '[{"id":"x"}]'}, 'needs')
        self.assertEqual(value, [{'id': 'x'}])
        self.assertEqual(repairs, ['complete_serialized_array_decoded'])
        with self.assertRaisesRegex(ValueError, 'malformed_serialized_needs'):
            ids.envelope({'needs': '[{"id":"x"}'}, 'needs')
        with self.assertRaisesRegex(ValueError, 'invalid_needs_envelope'):
            ids.envelope({'needs': [], 'score': 0.8}, 'needs')

    def test_source_authority_is_not_publication_time(self):
        b, need = self.fixture()
        source_rule = next(r for r in ids.rule_catalog(b['request']) if 'https://' in r['text'])
        need.update(dimension='source', target='agency.example', rule_ids=[source_rule['rule_id']])
        n = ids.bind_needs(b['request'], [need])['needs'][0]
        self.assertEqual(n['required_source_domains'], ['agency.example'])
        need['target'] = 'invented.example'
        n = ids.bind_needs(b['request'], [need])['needs'][0]
        self.assertEqual(n['required_source_domains'], [])

    def test_negative_evidence_retained_without_truth_claim(self):
        b, need = self.fixture(); plan = ids.bind_needs(b['request'], [need]); packet = base.reading_packet(b['pages'])
        row = {'need_id': 'stage', 'passage_ids': [packet['passages'][0]['passage_id']],
               'relation': 'counterevidence', 'fit': 'applicable', 'observation': 'pending', 'explanation': 'Application pending.'}
        review = ids.bind_review(plan, packet, [row], b['pages'])
        self.assertEqual(review['annotations'][0]['observations'], {'stage': 'pending'})
        self.assertFalse(review['annotations'][0]['truth_verified'])

    def test_unknown_without_passages_is_a_recorded_gap(self):
        b, need = self.fixture(); plan = ids.bind_needs(b['request'], [need]); packet = base.reading_packet(b['pages'])
        row = {'need_id': 'stage', 'passage_ids': [], 'relation': 'unknown', 'fit': 'unknown',
               'observation': '', 'explanation': 'No relevant delivered passage.'}
        review = ids.bind_review(plan, packet, [row], b['pages'])
        self.assertEqual(len(review['uncovered_assessments']), 1)
        bad = {**row, 'fit': 'applicable', 'observation': 'issued'}
        review = ids.bind_review(plan, packet, [bad], b['pages'])
        self.assertFalse(review['uncovered_assessments'])
        self.assertEqual(review['rejected_annotations'][0]['reason'], 'unbound_assertion_without_passages')

    def test_empty_plan_is_failed_and_skips_annotation(self):
        b, need = self.fixture(); calls, saved = [], []
        def execute(phase, *args):
            calls.append(phase); return {'needs': []}
        result = ids.agent_review(b, execute, saved.append)
        self.assertEqual(result['application_status'], 'failed_requirements')
        self.assertEqual(calls, ['plan_needs'])
        self.assertEqual(saved[-1]['phase'], 'ledger_saved')

    def test_two_stage_bridge_binds_without_offsets(self):
        b, need = self.fixture(); calls = []
        def execute(phase, prompt, payload, tool):
            calls.append(phase)
            if phase == 'plan_needs': return {'needs': [need]}
            return {'annotations': [{'need_id': 'stage',
                'passage_ids': [payload['reading']['passages'][0]['passage_id']], 'relation': 'counterevidence',
                'fit': 'applicable', 'observation': 'pending', 'explanation': 'Status is pending.'}]}
        result = ids.agent_review(b, execute, lambda r: None)
        self.assertEqual(calls, ['plan_needs', 'annotate_material'])
        self.assertEqual(result['application_status'], 'reviewed_with_gaps')

    def test_bad_annotation_cannot_pass_business_gate(self):
        b, need = self.fixture()
        def execute(phase, *args):
            if phase == 'plan_needs': return {'needs': [need]}
            return {'annotations': [{'need_id': 'stage', 'passage_ids': ['invented'], 'relation': 'support',
                'fit': 'applicable', 'observation': 'issued', 'explanation': 'Unbound claim.'}]}
        result = ids.agent_review(b, execute, lambda r: None)
        self.assertEqual(result['application_status'], 'failed_annotation')

    def test_forecast_fields_rejected(self):
        b, need = self.fixture(); need['probability'] = 0.9
        self.assertFalse(ids.bind_needs(b['request'], [need])['needs'])


if __name__ == '__main__':
    unittest.main()
