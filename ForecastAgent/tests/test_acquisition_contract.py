"""Domain-independent invariants, single-condition changes and failure isolation."""
import copy
import json
import unittest

from ForecastAgent.supplement import acquisition_contract as ac


def fixture():
    rules = 'Aster approval before 2026-08-01. Use agency.example.'
    origin = {'field': 'resolution_criteria', 'start': 0, 'end': len(rules), 'quote': rules}
    need = {'id': 'approval', 'condition': 'Capture approval stage and deadline',
        'critical': True, 'origin': origin, 'targets': {
            'entity': {'value': 'Aster', 'origin': origin},
            'stage': {'value': 'approval', 'origin': origin},
            'effective_time': {'value': 'before 2026-08-01', 'origin': origin}},
        'required_source_domains': ['agency.example'], 'source_origin': origin}
    body = 'Aster application submitted on July 1; approval remains pending.\n' * 12
    bundle = {'request': {'question': 'Will Aster receive approval?', 'resolution_criteria': rules},
              'pages': {'https://agency.example/notice': {'content': body}}}
    return bundle, need


class AcquisitionContracts(unittest.TestCase):
    def annotation(self, packet, **changes):
        return {'need_id': 'approval', 'passage_ids': [p['passage_id'] for p in packet['passages']],
                'relation': 'counterevidence', 'fit': 'applicable',
                'explanation': 'Application is pending; this informs stage without proving approval.',
                'observations': {'stage': 'application pending'}, **changes}

    def test_independent_requirement_rejection(self):
        b, n = fixture()
        bad = copy.deepcopy(n); bad['id'] = 'bad'; bad['origin']['end'] -= 1
        r = ac.requirements(b['request'], [bad, n])
        self.assertEqual([x['id'] for x in r['needs']], ['approval'])
        self.assertEqual(r['rejected_needs'][0]['reason'], 'unbound_rule_origin')

    def test_no_invented_source_restriction(self):
        b, n = fixture(); n['required_source_domains'] = ['invented.example']
        self.assertFalse(ac.requirements(b['request'], [n])['needs'])

    def test_individual_target_requires_rule_witness(self):
        b, n = fixture(); n['targets']['stage']['origin']['quote'] = 'Approved'
        self.assertFalse(ac.requirements(b['request'], [n])['needs'])

    def test_semantic_invention_not_certified_by_literal_origin(self):
        b, n = fixture(); n['targets']['stage']['value'] = 'a model misinterpretation'
        r = ac.requirements(b['request'], [n])
        self.assertFalse(r['needs'][0]['target_semantics_verified'])
        self.assertFalse(r['semantic_completeness_verified'])

    def test_more_than_three_passages_and_partial_negative_evidence(self):
        b, n = fixture(); packet = ac.reading_packet(b['pages'], unit_chars=100)
        self.assertGreater(len(packet['passages']), 3)
        review = ac.bind_annotations([n], packet, [self.annotation(packet)], b['pages'])
        self.assertEqual(len(review['annotations']), 1)
        row = ac.coverage([n], review)[0]
        self.assertEqual(row['state'], 'partially_covered')
        self.assertEqual(row['missing_dimensions'], ['effective_time', 'entity'])
        self.assertFalse(row['truth_verified'])

    def test_bad_record_does_not_destroy_good_record(self):
        b, n = fixture(); p = ac.reading_packet(b['pages'])
        good = self.annotation(p)
        r = ac.bind_annotations([n], p, [{**good, 'passage_ids': ['invented']}, good], b['pages'])
        self.assertEqual((len(r['annotations']), len(r['rejected_annotations'])), (1, 1))

    def test_changed_body_invalidates_old_binding(self):
        b, n = fixture(); p = ac.reading_packet(b['pages'])
        b['pages']['https://agency.example/notice']['content'] += ' Changed'
        r = ac.bind_annotations([n], p, [self.annotation(p)], b['pages'])
        self.assertEqual(r['rejected_annotations'][0]['reason'], 'saved_body_changed')

    def test_domain_boundary_not_substring(self):
        b, n = fixture(); b['pages'] = {'https://agency.example.attacker.test/notice': next(iter(b['pages'].values()))}
        p = ac.reading_packet(b['pages'])
        r = ac.bind_annotations([n], p, [self.annotation(p)], b['pages'])
        self.assertEqual(r['annotations'][0]['fit'], 'inapplicable')
        self.assertEqual(r['annotations'][0]['model_fit'], 'applicable')

    def test_json_small_record_remains_usable_and_exact(self):
        body = json.dumps({'date': '2026-08-01', 'value': 0, 'unit': 'USD'})
        p = ac.reading_packet({'data': {'content': body}})
        self.assertEqual(p['passages'][0]['text'], body)
        self.assertEqual(p['passages'][0]['reading']['kind'], 'json_document')

    def test_table_headers_and_omissions_preserved(self):
        body = '| Date | Value |\n| --- | --- |\n' + ''.join(f'| day{i} | {i} |\n' for i in range(40))
        p = ac.reading_packet({'table': {'content': body}}, max_chars=200, unit_chars=100)
        self.assertTrue(p['omitted'])
        self.assertLessEqual(p['delivered_chars'], 200)
        for w in p['passages']:
            self.assertEqual(body[w['start']:w['end']], w['text'])
            self.assertTrue(w['text'].endswith('\n'))
            if w['start'] > 0:
                self.assertIn('context_spans', w)

    def test_empty_interpretation_cannot_cover_dimension(self):
        b, n = fixture(); p = ac.reading_packet(b['pages'])
        r = ac.bind_annotations([n], p, [self.annotation(p, observations={'stage': ''})], b['pages'])
        self.assertFalse(r['annotations'])

    def test_full_coverage_is_model_claim_only(self):
        b, n = fixture(); p = ac.reading_packet(b['pages'])
        a = self.annotation(p, observations={k: v['value'] for k, v in n['targets'].items()})
        result = ac.run(b, [n], [a])
        self.assertEqual(result['coverage'][0]['state'], 'covered_by_model_claim')
        self.assertFalse(result['semantic_completeness_verified'])

    def test_conflict_retained_for_downstream(self):
        b, n = fixture(); p = ac.reading_packet(b['pages'])
        observations = {k: v['value'] for k, v in n['targets'].items()}
        result = ac.run(b, [n], [self.annotation(p, observations=observations),
                                  self.annotation(p, relation='support', observations=observations)])
        plan = ac.action_plan(result, remaining={})
        self.assertEqual(plan['actions'][0]['gap'], 'conflict')
        self.assertEqual(plan['actions'][0]['action'], 'handoff_analysis')

    def test_gap_routes_do_not_search_for_format_or_delivery_error(self):
        for gap, tool in [('format_invalid', 'repair_annotation'), ('saved_content_omitted', 'read_saved'),
                          ('read_failed', 'read_alternative'), ('material_inapplicable', 'discover')]:
            self.assertEqual(ac.next_action('n', gap, remaining={tool: 1})['action'], tool)

    def test_capacity_and_attempts_stop_loops(self):
        r = ac.next_action('n', 'format_invalid', remaining={'repair_annotation': 9}, review_attempts=1)
        self.assertEqual(r['action'], 'stop_with_gap')
        r = ac.next_action('n', 'read_failed', remaining={'read_alternative': 9},
                           attempted=[('n', 'read_failed', 'read_alternative')])
        self.assertEqual(r['action'], 'stop_with_gap')
        self.assertEqual(ac.next_action('n', 'unlocated', remaining={})['action'], 'stop_with_gap')

    def test_stage_vocabulary_is_not_domain_specific_enum(self):
        b, n = fixture(); n['targets']['stage']['value'] = 'mandate issued'
        self.assertEqual(len(ac.requirements(b['request'], [n])['needs']), 1)

    def test_inapplicable_material_is_retained_and_discovery_requested(self):
        b, n = fixture(); p = ac.reading_packet(b['pages'])
        result = ac.run(b, [n], [self.annotation(p, fit='inapplicable', relation='background')])
        self.assertEqual(len(result['review']['annotations']), 1)
        self.assertEqual(ac.action_plan(result, remaining={'discover': 1})['actions'][0]['action'], 'discover')

    def test_output_cannot_smuggle_forecast(self):
        b, n = fixture(); p = ac.reading_packet(b['pages'])
        result = ac.run(b, [n], [self.annotation(p, probability=0.9)])
        self.assertEqual(result['review']['rejected_annotations'][0]['reason'], 'unexpected_annotation_field')

    def test_packet_has_single_copy_and_portable_tools(self):
        b, n = fixture(); p = ac.reading_packet(b['pages'])
        tools = ac.agent_tools()
        self.assertEqual(len(tools), 2)
        self.assertNotIn('maxItems', tools[1]['function']['parameters']['properties']['annotations']['items']['properties']['passage_ids'])
        packet = ac.agent_packet(b['request'], [n], p)
        self.assertNotIn('raw_body', packet)
        self.assertFalse(packet['semantic_completeness_verified'])

    def test_rule_quote_reconstructed_without_model_retyping(self):
        b, n = fixture(); del n['origin']['quote']
        # Shared origins in this fixture also exercise target reconstruction.
        result = ac.requirements(b['request'], [n])
        self.assertEqual(result['needs'][0]['origin']['quote'], b['request']['resolution_criteria'])
        self.assertIn('field_sha256', result['needs'][0]['targets']['stage']['origin'])

    def test_rejected_need_is_an_actionable_gap(self):
        b, n = fixture(); n['origin']['start'] = -1
        result = ac.run(b, [n], [])
        plan = ac.action_plan(result, remaining={'repair_requirements': 1})
        self.assertEqual(plan['actions'][0]['action'], 'repair_requirements')
        self.assertTrue(plan['requirement_review_pending'])

    def test_agent_bridge_two_calls_and_saved_phase_boundaries(self):
        b, n = fixture(); calls, saved = [], []
        def execute(phase, prompt, payload, tool):
            calls.append(phase)
            if phase == 'plan_needs':
                return {'needs': [n]}
            self.assertNotIn('model', payload)
            return {'annotations': [self.annotation(payload['reading'])]}
        result = ac.agent_review(b, execute, saved.append)
        self.assertEqual(calls, ['plan_needs', 'annotate_material'])
        self.assertEqual(result['logical_model_decisions'], 2)
        self.assertEqual([s['phase'] for s in saved], ['planning_reply_saved', 'reading_prepared',
                                                    'annotation_reply_saved', 'ledger_saved'])
        self.assertEqual(result['coverage'][0]['state'], 'partially_covered')

    def test_invalid_agent_envelope_preserved_without_retry(self):
        b, n = fixture(); saved = []
        with self.assertRaisesRegex(ValueError, 'invalid_planning_envelope'):
            ac.agent_review(b, lambda *args: {'garbage': True}, saved.append)
        self.assertEqual(saved[0]['phase'], 'planning_reply_saved')

    def test_no_readable_body_skips_annotation_model_call(self):
        b, n = fixture(); b['pages'] = {}; calls = []
        def execute(*args):
            calls.append(args[0]); return {'needs': [n]}
        result = ac.agent_review(b, execute, lambda state: None)
        self.assertEqual(calls, ['plan_needs'])
        self.assertEqual(result['coverage'][0]['state'], 'unreviewed')


if __name__ == '__main__':
    unittest.main()
