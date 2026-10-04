"""Required-field evidence, stage and explanation consistency regression tests."""
import copy
import json
import unittest
from pathlib import Path
from ForecastAgent.supplement.field_contract import evaluate, span_catalog

MANIFEST = Path(__file__).resolve().parents[1] / 'experiments/MATERIAL_FIELD_REGRESSION10.json'


class FieldContractTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(MANIFEST.read_text(encoding='utf-8'))['cases']
        self.case = next(c for c in self.cases if c['question_id'] == '24819')

    def observed(self, case=None):
        case = case or self.case
        result = copy.deepcopy(case['observation'])
        result.update(material_verdict='matched', explanation_verdict='matched')
        result['field_evidence'] = [dict(field_id=r['id'], verdict='matched',
            explanation_verdict='matched', value=result['quote'][:60],
            observed_stage=(r['allowed_stages'] or ['not_applicable'])[0],
            quote=result['quote'], explanation='The requested material field is recorded.')
            for r in case['contract']['required_fields']]
        return result

    def test_negative_outcome_status_remains_usable(self):
        observed = self.observed()
        self.assertEqual(observed['evidence_relation'], 'counterevidence')
        r = evaluate(self.case['contract'], self.case['source'], observed)
        self.assertEqual(r['status'], 'matched')
        self.assertTrue(r['eligible_for_need_closure'])
        self.assertEqual(len(r['field_bindings']), 2)
        for field in r['field_bindings']:
            for span in field['quote_binding']['spans']:
                self.assertEqual(span['text'], self.case['source']['text'][span['start']:span['end']])

    def test_application_cannot_satisfy_granted_approval_even_with_true_axes(self):
        case = next(c for c in self.cases if c['question_id'] == '43492')
        observed = self.observed(case)
        observed['document_role'] = 'status_record'
        observed['fit_axes'] = {a: True for a in observed['fit_axes']}
        observed['field_evidence'][1]['observed_stage'] = 'applied'
        r = evaluate(case['contract'], case['source'], observed)
        self.assertEqual(r['status'], 'mismatched')
        self.assertIn('field_stage_mismatch:field_metric', r['issues'])
        self.assertFalse(r['eligible_for_need_closure'])

    def test_field_explanation_and_final_verdict_conflicts_block_acceptance(self):
        observed = self.observed()
        observed['field_evidence'][0]['explanation_verdict'] = 'mismatched'
        self.assertEqual(evaluate(self.case['contract'], self.case['source'], observed)['status'], 'uncertain')
        observed = self.observed()
        observed['explanation_verdict'] = 'mismatched'
        self.assertEqual(evaluate(self.case['contract'], self.case['source'], observed)['status'], 'uncertain')

    def test_missing_duplicate_unknown_and_unbound_witnesses(self):
        for mutate in (
                lambda o: o['field_evidence'].pop(),
                lambda o: o['field_evidence'].append(copy.deepcopy(o['field_evidence'][0])),
                lambda o: o['field_evidence'][0].update(field_id='unrelated_facility_size'),
                lambda o: o['field_evidence'][0].update(value='Unquoted made-up value'),
                lambda o: o['field_evidence'][0].update(quote='An invented sentence absent from the original body.')):
            observed = self.observed()
            mutate(observed)
            r = evaluate(self.case['contract'], self.case['source'], observed)
            self.assertNotEqual(r['status'], 'matched')
            self.assertFalse(r['eligible_for_need_closure'])

    def test_legacy_booleans_cannot_invent_field_witnesses(self):
        r = evaluate(self.case['contract'], self.case['source'], self.case['observation'])
        self.assertEqual(r['status'], 'uncertain')
        self.assertFalse(r['eligible_for_need_closure'])

    def test_supporting_material_cannot_close_target_need(self):
        case = next(c for c in self.cases if c['question_id'] == '43333')
        r = evaluate(case['contract'], case['source'], self.observed(case))
        self.assertEqual(r['status'], 'matched')
        self.assertFalse(r['eligible_for_need_closure'])

    def test_program_contract_cannot_omit_required_axis(self):
        contract = copy.deepcopy(self.case['contract'])
        contract['required_fields'].pop()
        r = evaluate(contract, self.case['source'], self.observed())
        self.assertEqual(r['status'], 'uncertain')

    def test_indexed_ranges_do_not_depend_on_model_quotes_or_values(self):
        observed = self.observed()
        observed.pop('quote')
        observed['span_ids'] = ['span-001']
        for field in observed['field_evidence']:
            field.pop('quote')
            field.pop('value')
            field.update(span_ids=['span-001'], proposed_value='Unverified paraphrase for display only')
        result = evaluate(self.case['contract'], self.case['source'], observed)
        self.assertEqual(result['status'], 'matched')
        for field in result['field_bindings']:
            for span in field['quote_binding']['spans']:
                self.assertEqual(span['text'], self.case['source']['text'][span['start']:span['end']])
        observed['field_evidence'][0]['span_ids'] = ['invented-id']
        self.assertEqual(evaluate(self.case['contract'], self.case['source'], observed)['status'], 'uncertain')

    def test_catalog_covers_original_body_without_gaps(self):
        for case in self.cases:
            spans = span_catalog(case['source'])
            self.assertEqual(''.join(s['text'] for s in spans), case['source']['text'])
            for i, span in enumerate(spans):
                self.assertEqual(span['start'], spans[i-1]['end'] if i else 0)
                self.assertEqual(span['capture_start'], case['source']['start'] + span['start'])


if __name__ == '__main__':
    unittest.main()
