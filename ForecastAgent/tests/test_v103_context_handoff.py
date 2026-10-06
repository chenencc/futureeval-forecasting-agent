"""Original context, view identity and quota-free delivery regressions."""

import copy
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import context_handoff as delivery
from ForecastAgent.acquisition import handoff
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.tests.test_material_handoff import bank, fixture


class ContextHandoffTests(TestCase):
    def test_originals_and_all_old_coordinates_survive_without_provider_calls(self):
        bundle = fixture('Header 2026 USD\n\n' + ('Alpha and Beta original paragraph.\n\n' * 90))
        bundle['excerpts'] = [bank(bundle, None, 18, 600)]
        original = copy.deepcopy(bundle)
        with patch('urllib.request.urlopen', side_effect=AssertionError('No transport')):
            state, audit = delivery.pack(bundle)
        old, _ = delivery.baseline(bundle)
        self.assertEqual(bundle, original)
        self.assertEqual(audit['source_integrity_errors'], [])
        self.assertEqual(audit['old_visible_text_removed'], [])
        self.assertEqual(state['question'], old['question'])
        self.assertEqual(state['instruction'], old['instruction'])
        self.assertLessEqual(chain.request_bytes(state), chain.FIRST_BYTES)
        self.assertEqual(audit_spans(bundle, state), [])

    def test_complete_paragraph_keeps_a_limiting_following_sentence(self):
        text = 'Dated original header.\n\nAlpha crossed the threshold. However, this applies only to the provisional measure.\n\nDifferent topic.'
        start = text.index('Alpha')
        a, b = delivery.paragraph(text, start, start + 25)
        self.assertIn('However', text[a:b])
        self.assertNotIn('Different topic', text[a:b])

    def test_extracted_table_header_and_row_are_admitted_together(self):
        table = 'Name | Amount in USD\n---- | ----\nAlpha | 27\nBeta | 18\n'
        bundle = fixture('Original report June 30, 2026.\n', documents=[
            {'page_content': table, 'metadata': {'format': 'html_table', 'table_index': 1}}])
        start = table.index('Alpha')
        bundle['excerpts'] = [bank(bundle, 1, start, table.index('Beta'))]
        state, audit = delivery.pack(bundle)
        original = ''.join(e['text'] for e in state['evidence'] if e.get('document_index') == 1)
        self.assertIn('Name | Amount in USD', original)
        self.assertIn('---- | ----', original)
        self.assertIn('Alpha | 27', original)
        self.assertEqual(audit_spans(bundle, state), [])

    def test_stale_excerpt_is_quarantined_without_discarding_the_valid_body(self):
        bundle = fixture('Original Alpha and Beta report.\n')
        item = bank(bundle, None, 0, 15)
        item['source_sha256'] = 'stale'
        bundle['excerpts'] = [item]
        state, audit = delivery.pack(bundle)
        self.assertEqual(len(audit['invalid_banks']), 1)
        self.assertTrue(state['evidence'])
        self.assertEqual(audit['old_visible_text_removed'], [])

    def test_novel_char_count_uses_coordinates_not_renamed_ids(self):
        before = {'evidence': [{'evidence_id': 'E1', 'source_id': 'S1', 'start': 0, 'end': 20}]}
        after = {'evidence': [{'evidence_id': 'H1', 'source_id': 'S1', 'start': 0, 'end': 20}]}
        self.assertEqual(delivery.novel_chars(before, after), 0)
        after['evidence'][0]['end'] = 23
        self.assertEqual(delivery.novel_chars(before, after), 3)

    def test_large_unbroken_context_is_reported_not_silently_cut(self):
        bundle = fixture('Alpha ' + 'x' * 12000)
        bundle['excerpts'] = [bank(bundle, None, 2000, 2010)]
        state, audit = delivery.pack(bundle)
        self.assertTrue(any(v['reason'] == 'oversized_complete_context' for v in audit['omissions']))
        self.assertEqual(audit['old_visible_text_removed'], [])
        self.assertLessEqual(chain.request_bytes(state), chain.FIRST_BYTES)

    def test_conditional_projection_preserves_first_read_and_has_exact_new_ranges(self):
        bundle = fixture('Header 2026.\n\n' + ('Alpha original paragraph.\n\n' * 450))
        first, _ = delivery.pack(bundle)
        second, audit = delivery.extend(bundle, first, ['time_window'])
        self.assertEqual(audit['old_visible_text_removed'], [])
        self.assertEqual(audit_spans(bundle, second), [])
        self.assertLessEqual(chain.request_bytes(second), chain.SECOND_BYTES)
        self.assertEqual(audit['new_coordinate_chars'], delivery.novel_chars(first, second))

    def test_outcome_field_is_rejected_before_any_packing(self):
        bundle = fixture('Original report.')
        bundle['request']['resolution'] = 1
        with self.assertRaises(ValueError):
            delivery.pack(bundle)
