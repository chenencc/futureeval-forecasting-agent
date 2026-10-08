"""Offline counterexamples for bounded open-tournament orchestration."""
import copy
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.analysis.pilot import save
from ForecastAgent.market_pulse import batch_analysis as batch, open_campaign as campaign
from ForecastAgent.market_pulse import facts


class OpenCampaignTests(unittest.TestCase):
    def test_decimal_currency_suffix_cannot_backtrack_to_integer_prefix(self):
        text = 'Quarterly revenue rose 26% to $28.2B. Prior revenue was $22.4B.'
        tokens = facts.numeric_tokens(text)
        self.assertEqual([t['value'] for t in tokens], [26, 28.2, 22.4])
        for token in tokens:
            self.assertEqual(text[token['start']:token['end']], token['raw'])
        self.assertEqual([t['value'] for t in facts.numeric_tokens('| Revenue | $28.2B |')], [28.2])
        self.assertEqual(facts.numeric_tokens('Revenue model B28.2 and v2.3'), [])

    def test_pagination_cannot_escape_tournament_or_official_host(self):
        path = campaign.scoped_feed_path(
            'https://www.metaculus.com/api/posts/?tournaments=33131&offset=100')
        self.assertIn('tournaments=33131', path)
        for url in ('https://example.com/api/posts/?tournaments=33131',
                    'https://www.metaculus.com/api/posts/?tournaments=33121',
                    'https://www.metaculus.com/api/comments/?tournaments=33131'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                campaign.scoped_feed_path(url)

    def test_target_eps_row_is_reserved_before_unrelated_financial_rows(self):
        rows = [{'row': 'Net income $100 million', 'record_id': str(i), 'priority': i}
                for i in range(35)]
        rows += [{'row': 'Shares used in computation of diluted earnings per share 100',
                  'record_id': 'shares', 'priority': 0},
                 {'row': 'Diluted earnings per share $2.10', 'record_id': 'eps', 'priority': 90}]
        table = {'candidates': rows}; before = copy.deepcopy(table)
        ranked = batch.prioritize_target_rows(table, {'metric': 'gaap_diluted_eps'})
        self.assertEqual(ranked['candidates'][0]['record_id'], 'eps')
        self.assertEqual(table, before)
        self.assertEqual(sorted(r['record_id'] for r in ranked['candidates']),
                         sorted(r['record_id'] for r in table['candidates']))

    def test_revenue_target_does_not_prioritize_eps(self):
        table = {'candidates': [{'row': 'Diluted EPS $4', 'priority': 0},
                                {'row': 'Total net sales $15 billion', 'priority': 30}]}
        ranked = batch.prioritize_target_rows(table, {'metric': 'quarterly_revenue'})
        self.assertIn('net sales', ranked['candidates'][0]['row'])

    def test_span_merge_keeps_all_exposed_source_characters_and_aliases(self):
        text = '0123456789abcdefghij'
        bundle = {'pages': {'https://issuer.example/report': {'content': text}}}
        def ref(ident, start, end):
            return {'ref_id': ident, 'url': 'https://issuer.example/report',
                    'document_index': None, 'field': None, 'text_sha256': 'original',
                    'start': start, 'end': end, 'original_text': text[start:end]}
        state = {'original_evidence_library': [ref('A', 2, 9), ref('B', 6, 13), ref('C', 17, 20)],
                 'variables': [{'source_ref_ids': ['A', 'B', 'C']}],
                 'source_coverage': {'superseded': True}, 'saved_source_coverage': {'latest': True}}
        merged = batch.merge_original_spans(bundle, state)
        self.assertEqual([r['original_text'] for r in merged['original_evidence_library']],
                         [text[2:13], text[17:20]])
        self.assertEqual(len(merged['variables'][0]['source_ref_ids']), 2)
        self.assertNotIn('source_coverage', merged)
        self.assertTrue(merged['saved_source_coverage']['latest'])
        self.assertEqual(set(merged['packing_audit']['previous_ref_to_merged_ref']), {'A', 'B', 'C'})

    def test_mutated_original_span_cannot_be_packed(self):
        bundle = {'pages': {'https://issuer.example/report': {'content': 'original'}}}
        state = {'original_evidence_library': [{'ref_id': 'A', 'url': 'https://issuer.example/report',
                    'start': 0, 'end': 8, 'original_text': 'modified', 'text_sha256': 'x'}],
                 'variables': []}
        with self.assertRaises(ValueError):
            batch.merge_original_spans(bundle, state)

    def test_ready_saved_candidate_survives_a_later_failed_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); task = root / 'tasks/1'
            save(task / 'analysis/result.json', {'manual_delivery_candidate': True, 'payload': {'a': 1}})
            save(task / 'analysis-failed/result.json', {'status': 'needs_review'})
            folder, result = campaign.latest_result(root, '1')
            self.assertEqual(folder.name, 'analysis')
            self.assertTrue(result['manual_delivery_candidate'])


if __name__ == '__main__':
    unittest.main()
