"""Counterfactual provenance and condition-scope regressions, without providers."""
import copy
import unittest
from ForecastAgent.supplement import acquisition_contract as base
from ForecastAgent.supplement import acquisition_ids as ids
from ForecastAgent.supplement import acquisition_provenance as v3


class Provenance(unittest.TestCase):
    def fixture(self):
        request = {'question': 'Was a qualifying restriction kept through September 1?',
            'resolution_criteria': 'Large facilities mean 50 MW or more. Restrictions must remain through September 1.'}
        catalog = ids.rule_catalog(request)
        needs = [{'id': 'scope', 'condition': 'A large facility restriction remained through the deadline.',
                  'critical': True, 'dimension': 'entity', 'target': 'Texas',
                  'rule_ids': [r['rule_id'] for r in catalog]}]
        body = ('On August 3, Texas announced a restriction for new connections.\n'
                'The report describes publication and scope, not subsequent rescission history.\n'
                'Daily close is 12.45 USD; adjustment details are not included.\n')
        pages = {'https://agency.example/directive': {'content': body}}
        plan = ids.bind_needs(request, needs)
        packet = base.reading_packet(pages)
        row = {'need_id': 'scope', 'passage_ids': [packet['passages'][0]['passage_id']],
            'rule_ids': [], 'origin': 'source_fact', 'relation': 'support', 'fit': 'applicable',
            'observation': 'Texas', 'witness': 'On August 3, Texas announced a restriction for new connections.',
            'explanation': 'Entity is reported; continued effect is not established.'}
        return request, plan, pages, packet, row

    def review(self, **changes):
        _, plan, pages, packet, row = self.fixture()
        row.update(changes)
        return plan, v3.bind_review(plan, packet, [row], pages)

    def test_entity_match_never_closes_compound_condition(self):
        plan, review = self.review()
        status = v3.coverage(plan, review)[0]
        self.assertEqual(status['state'], 'field_literal_observed')
        self.assertEqual(status['condition_coverage'], 'unverified')
        self.assertFalse(review['annotations'][0]['truth_verified'])

    def test_rule_threshold_retained_as_rule_not_market_fact(self):
        _, plan, pages, packet, row = self.fixture()
        rule = next(r for r in plan['rule_catalog'] if '50 MW' in r['text'])
        row.update(origin='rule_constant', passage_ids=[], rule_ids=[rule['rule_id']],
                   observation='50 MW', witness='Large facilities mean 50 MW or more.')
        review = v3.bind_review(plan, packet, [row], pages)
        self.assertEqual(v3.coverage(plan, review)[0]['state'], 'rule_defined')
        self.assertEqual(review['annotations'][0]['bindings'], [])

    def test_rule_witness_cannot_be_cited_as_page_fact(self):
        _, review = self.review(observation='50 MW', witness='Large facilities mean 50 MW or more.')
        self.assertEqual(review['rejected_annotations'][0]['reason'], 'witness_not_uniquely_bound')
        self.assertEqual(review['raw_annotations'][0]['observation'], '50 MW')

    def test_rule_constant_cannot_mix_source_reference(self):
        _, plan, pages, packet, row = self.fixture()
        row.update(origin='rule_constant', rule_ids=[plan['rule_catalog'][0]['rule_id']])
        r = v3.bind_review(plan, packet, [row], pages)
        self.assertEqual(r['rejected_annotations'][0]['reason'], 'rule_constant_requires_rules_only')

    def test_silence_across_interval_remains_inference(self):
        plan, review = self.review(origin='inference', observation='Not rescinded through September 1', witness='',
                                  explanation='The August page is silent; later interval coverage is missing.')
        self.assertEqual(v3.coverage(plan, review)[0]['state'], 'interpretation_only')
        self.assertFalse(review['annotations'][0]['value_literal_in_witness'])

    def test_copied_target_date_is_not_literal_observation(self):
        plan, review = self.review(observation='September 1')
        self.assertEqual(v3.coverage(plan, review)[0]['state'], 'source_interpretation')
        self.assertFalse(review['annotations'][0]['value_literal_in_witness'])

    def test_close_equivalence_is_not_verified_by_literal_price(self):
        plan, review = self.review(observation='Adjusted Close 12.45 USD',
             witness='Daily close is 12.45 USD; adjustment details are not included.')
        self.assertEqual(v3.coverage(plan, review)[0]['state'], 'source_interpretation')
        self.assertFalse(review['annotations'][0]['interpretation_verified'])

    def test_normalized_value_is_retained_not_silently_literalized(self):
        _, review = self.review(observation='State of Texas')
        self.assertEqual(len(review['annotations']), 1)
        self.assertFalse(review['annotations'][0]['value_literal_in_witness'])

    def test_explicit_negative_text_is_retained(self):
        _, review = self.review(relation='counterevidence', observation='not included',
            witness='Daily close is 12.45 USD; adjustment details are not included.')
        self.assertEqual(review['annotations'][0]['relation'], 'counterevidence')

    def test_background_is_retained(self):
        plan, review = self.review(relation='background')
        self.assertEqual(v3.coverage(plan, review)[0]['state'], 'context_only')
        self.assertEqual(len(review['annotations']), 1)

    def test_bad_row_does_not_discard_good_row_or_body(self):
        _, plan, pages, packet, row = self.fixture()
        before = copy.deepcopy(pages)
        bad = {**row, 'passage_ids': ['fabricated']}
        review = v3.bind_review(plan, packet, [bad, row], pages)
        self.assertEqual(len(review['annotations']), 1)
        self.assertEqual(len(review['rejected_annotations']), 1)
        self.assertEqual(review['raw_annotations'], [bad, row])
        self.assertEqual(pages, before)

    def test_changed_capture_is_rejected(self):
        _, plan, pages, packet, row = self.fixture()
        pages[next(iter(pages))]['content'] += 'Changed after packet delivery.'
        r = v3.bind_review(plan, packet, [row], pages)
        self.assertEqual(r['rejected_annotations'][0]['reason'], 'saved_body_changed')

    def test_unknown_assessment_is_explicit_gap(self):
        plan, review = self.review(origin='unknown', passage_ids=[], observation='', witness='',
                                  fit='unknown', relation='unknown')
        self.assertEqual(v3.coverage(plan, review)[0]['state'], 'reviewed_uncovered')

    def test_unknown_with_target_is_rejected(self):
        _, review = self.review(origin='unknown', passage_ids=[], witness='', fit='unknown', relation='unknown')
        self.assertEqual(review['rejected_annotations'][0]['reason'], 'unknown_with_assertion')

    def test_derivation_is_not_a_direct_observation(self):
        plan, review = self.review(origin='derived', witness='', observation='Qualifying by subtraction')
        self.assertEqual(v3.coverage(plan, review)[0]['state'], 'interpretation_only')

    def test_review_only_makes_one_decision_and_no_semantic_retry(self):
        request, plan, pages, _, row = self.fixture()
        calls, checkpoints = [], []
        def execute(phase, prompt, payload, tool):
            calls.append(phase)
            return {'annotations': [row]}
        result = v3.review_saved({'request': request, 'pages': pages}, plan, execute, checkpoints.append)
        self.assertEqual(calls, ['annotate_provenance'])
        self.assertEqual(result['application_status'], 'reviewed_with_gaps')
        self.assertEqual(result['action_plan']['actions'][0]['action'], 'handoff_analysis')
        self.assertFalse(result['action_plan']['reserves_or_executes_tools'])


if __name__ == '__main__':
    unittest.main()
