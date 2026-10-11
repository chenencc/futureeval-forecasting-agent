"""Literal source binding does not certify source roles or target conclusions."""
import copy
import json
import unittest
from ForecastAgent.research_loop import source_facets as sf


class SourceFacetTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{'evidence_id': 'R1', 'url': 'https://example.org/news',
                         'body_sha256': 'original', 'start': 40, 'end': 150,
                         'text': 'Published Wednesday 16 September. Classes closed Thursday. Council reported totals.'}]
        self.answer = {'sources': [{'reference_id': 'R1', 'coverage': 'content_excerpt',
            'coverage_quote': 'Classes closed Thursday.', 'facts': [
                {'kind': 'publication_time', 'quote': 'Published Wednesday 16 September.', 'value': '16 September'},
                {'kind': 'event_time', 'quote': 'Classes closed Thursday.', 'value': 'Thursday'}],
            'unknowns': ['The calendar date of Thursday is not stated.']}]}

    def test_fields_bound_but_roles_remain_unverified(self):
        result = sf.bind(self.sources, self.answer)
        field = result['sources'][0]['facts'][0]
        self.assertTrue(field['binding']['literal_binding_verified'])
        self.assertFalse(field['binding']['meaning_verified'])
        self.assertFalse(result['sources'][0]['coverage_verified'])
        self.assertFalse(result['target_effects_generated'])
        self.assertEqual(field['binding']['local_positions'], [0])

    def test_invented_quote_rejected_without_losing_valid_sibling(self):
        self.answer['sources'][0]['facts'][0]['quote'] = 'An invented date.'
        result = sf.bind(self.sources, self.answer)['sources'][0]
        self.assertEqual(len(result['facts']), 1)
        self.assertEqual(len(result['rejected_facts']), 1)

    def test_normalized_inferred_value_cannot_masquerade_as_literal(self):
        self.answer['sources'][0]['facts'][1]['value'] = '17 September'
        self.assertEqual(len(sf.bind(self.sources, self.answer)['sources'][0]['facts']), 1)

    def test_roles_are_not_programmatically_asserted_correct(self):
        self.answer['sources'][0]['facts'][0]['kind'] = 'event_time'
        result = sf.bind(self.sources, self.answer)
        self.assertEqual(len(result['sources'][0]['facts']), 2)
        self.assertFalse(result['meaning_verified'])

    def test_cross_source_quote_rejected(self):
        self.sources.append({'evidence_id': 'R2', 'text': 'Official close 12000.'})
        self.answer['sources'][0]['facts'][0].update(quote='Official close 12000.', value='12000')
        result = sf.bind(self.sources, self.answer)
        self.assertEqual(len(result['sources'][0]['facts']), 1)
        self.assertEqual(result['unprocessed_reference_ids'], ['R2'])

    def test_navigation_and_caption_do_not_certify_event_status(self):
        for coverage in ('navigation_only', 'caption_text'):
            with self.subTest(coverage=coverage):
                answer = copy.deepcopy(self.answer)
                answer['sources'][0].update(coverage=coverage, facts=[{
                    'kind': 'event_status', 'quote': 'Classes closed Thursday.', 'value': 'closed'}])
                result = sf.bind(self.sources, answer)['sources'][0]
                self.assertEqual(result['facts'], [])
                self.assertEqual(len(result['rejected_facts']), 1)

    def test_duplicate_sources_are_unprocessed(self):
        self.sources.append({'evidence_id': 'R2', 'text': 'Another source.'})
        self.answer['sources'].append(copy.deepcopy(self.answer['sources'][0]))
        result = sf.bind(self.sources, self.answer)
        self.assertEqual(result['sources'], [])
        self.assertEqual(result['unprocessed_reference_ids'], ['R1', 'R2'])

    def test_repeated_literal_locations_explicitly_ambiguous(self):
        self.sources[0]['text'] += ' Classes closed Thursday.'
        result = sf.bind(self.sources, self.answer)['sources'][0]['facts'][1]
        self.assertTrue(result['binding']['location_ambiguous'])
        self.assertEqual(len(result['binding']['local_positions']), 2)

    def test_no_old_graph_or_choice_answers_in_request(self):
        request = sf.messages({'title': 'Will classes close?'}, '2026-10-11', self.sources)
        payload = json.loads(request[1]['content'])
        self.assertNotIn('current_map', payload)
        self.assertNotIn('checks', payload)
        self.assertEqual(payload['originals'], self.sources)

    def test_identity_drift_rejected_before_dispatch(self):
        with self.assertRaises(ValueError):
            sf.originals([{'originals': self.sources}, {'originals': [{**self.sources[0], 'text': 'different'}]}])


if __name__ == '__main__':
    unittest.main()
