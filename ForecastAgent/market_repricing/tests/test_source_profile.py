"""Source-role safety, finite routing and unknown-scope negative controls."""
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.source_profile import profile, catalog_role, applicability
from ForecastAgent.market_repricing.evidence_capture import candidates, select_candidates


def section(text, role='unknown', tag='div'):
    return {'text': text, 'role': role, 'tag': tag, 'xpath': '/html/body/' + tag, 'source_sha256': 'fixture'}


def structure(rows, truncated=False):
    return {'sections': rows, 'truncated': truncated, 'explicit_date_metadata': []}


PAGE = {'content_type': 'text/html', 'raw_response_base64': 'fixture', 'sha256': 'fixture', 'content': 'Original text'}


class SourceProfile(unittest.TestCase):
    def test_header_support_phone_is_not_an_election_record(self):
        rows = [section('Search', 'header'), section('Central support 1299', 'footer')]
        with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)):
            observed = profile(PAGE, 'https://example.gov/agency')
        self.assertEqual(observed['role'], 'navigation_shell')
        self.assertFalse(observed['usable_text'])
        self.assertFalse(observed['semantic_adequacy_verified'])

    def test_unknown_or_truncated_markup_is_not_automatically_rejected(self):
        for rows, truncated in (([section('Official notice has been published')], False), ([section('Menu', 'header')], True)):
            with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows, truncated)):
                observed = profile(PAGE, 'https://example.gov/notice')
            self.assertIsNone(observed['qualifies_full_body'])

    def test_misnested_video_description_is_not_discarded_by_markup_role(self):
        rows = [section('Video description: the court identified procedural defects.', 'header')]
        with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)):
            observed = profile(PAGE | {'content': 'Video description with exact legal facts. ' * 40}, 'https://news.example/video')
        self.assertEqual(observed['role'], 'markup_scope_unknown_retained')
        self.assertIsNone(observed['qualifies_full_body'])

    def test_header_wrapped_numeric_record_remains_unknown_not_excluded(self):
        rows = [section('2026-10-13: 5.1%', 'header')]
        with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)):
            observed = profile(PAGE | {'content': rows[0]['text']}, 'https://example.gov/rate')
        self.assertIsNone(observed['qualifies_full_body'])

    def test_preview_keeps_headline_and_standfirst_as_context(self):
        rows = [section('Election positions', 'article', 'h1'), section('A headline summary', 'article', 'h2'), section('\u200c', 'article')]
        with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)):
            observed = profile(PAGE | {'content': 'Election positions\nA headline summary\n' + '\u200c\n' * 25}, 'https://news.example/article')
        self.assertEqual(observed['role'], 'article_preview')
        self.assertTrue(observed['usable_text']); self.assertFalse(observed['qualifies_full_body'])

    def test_short_dated_numeric_notice_and_html_rows_are_preserved(self):
        for record in ({}, {'rows': [{'rate': 5.1}]}):
            rows = [section('Official rate', 'article', 'h1'), section('2026-10-13: 5.1%', 'article')]
            with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)):
                observed = profile(PAGE | record, 'https://example.gov/rate')
            self.assertEqual(observed['role'], 'dated_or_structured_record_original')
            self.assertIsNone(observed['qualifies_full_body'])

    def test_complete_article_and_data_table_are_not_previews(self):
        for rows in ([section('Title', 'article', 'h1'), section('A complete notice states the policy and its effective date.', 'article', 'p')],
                     [section('Title', 'article', 'h1'), section('Date | Rate\n2026-10-13 | 5.1', 'main', 'table')]):
            with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)):
                observed = profile(PAGE, 'https://example.gov/notice')
            self.assertIsNone(observed['qualifies_full_body'])

    def test_document_catalog_retains_routing_context_without_becoming_full_document(self):
        rows = [section('Documents', tag='h1'), section('A.pdf B.pdf C.pdf')]
        with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)), patch(
                'ForecastAgent.market_repricing.source_profile.observed_details', return_value=[{'url': str(i)} for i in range(3)]):
            observed = catalog_role(PAGE, 'https://example.gov/documents')
        self.assertEqual(observed['role'], 'document_catalog')
        self.assertTrue(observed['usable_text']); self.assertFalse(observed['qualifies_full_body'])

    def test_query_in_sidebar_does_not_certify_heading_or_actual_event(self):
        rows = [section('Healthcare financing', tag='h1')]
        with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)):
            observed = applicability(PAGE | {'content': 'Healthcare financing. Sidebar: diesel export policy.'},
                'https://example.gov/medical', {'title': 'Documents', 'search_queries': ['diesel export']}, {'question': 'Diesel exports in 2026?'})
        self.assertTrue(observed['scope_review_required'])
        self.assertEqual(observed['literal_scope']['query_terms_in_headings'], [])
        self.assertEqual(observed['literal_scope']['query_terms_elsewhere_only'], ['diesel', 'export'])
        self.assertTrue(observed['automatic_followup_requires_scope_review'])
        self.assertFalse(observed['semantic_adequacy_verified'])

    def test_foreign_language_and_old_baseline_only_raise_review_signals(self):
        rows = [section('Bilan historique 2023', tag='h1')]
        with patch('ForecastAgent.market_repricing.source_profile._structure', return_value=structure(rows)):
            observed = applicability(PAGE, 'https://example.fr/report', {'search_queries': ['Election turnout']}, {'question': 'Election turnout 2026?'})
        self.assertTrue(observed['scope_review_required'])
        self.assertIsNone(observed['qualifies_full_body'])
        self.assertEqual(observed['literal_scope']['heading_year_literals'], ['2023'])

    def test_bounded_selection_spreads_needs_and_keeps_exact_designated_source(self):
        leads = [
            {'url': 'https://official.example/a', 'host': 'official.example', 'roles': ['rules_designated'], 'need_ids': ['N1'], 'priority': 100},
            {'url': 'https://official.example/b', 'host': 'official.example', 'roles': [], 'need_ids': ['N1'], 'priority': 50},
            {'url': 'https://news.example/c', 'host': 'news.example', 'roles': [], 'need_ids': ['N2'], 'priority': 20},
        ]
        self.assertEqual([row['url'] for row in select_candidates(leads, limit=2)], ['https://official.example/a', 'https://news.example/c'])
        with self.assertRaises(ValueError): select_candidates(leads, limit=9)

    def test_official_host_alone_does_not_outrank_literal_target_news(self):
        bundle = {'request': {'question': 'Russia diesel export policy'}, 'searches': [{'query': 'Diesel export', 'results': [
            {'url': 'http://government.ru/docs/1', 'title': 'Healthcare payments'},
            {'url': 'https://news.example/policy', 'title': 'Russia diesel export policy'}]}]}
        selected = candidates(bundle)
        self.assertEqual(selected[0]['url'], 'https://news.example/policy')
        self.assertEqual(selected[0]['search_queries'], ['Diesel export'])

    def test_deeper_native_links_remain_known_without_automatic_recursion(self):
        bundle = {'source_leads': {
            'https://example.org/a': {'url': 'https://example.org/a', 'origin': 'search'},
            'https://example.org/b.pdf': {'url': 'https://example.org/b.pdf', 'origin': 'observed_document_detail', 'depth': 1},
            'https://example.org/c.pdf': {'url': 'https://example.org/c.pdf', 'origin': 'page_link', 'parent_url': 'https://example.org/b.pdf'},
        }}
        last = next(row for row in candidates(bundle) if row['url'].endswith('c.pdf'))
        self.assertFalse(last['automatic_capture_eligible'])
        self.assertEqual(last['depth'], 2)

    def test_trailing_slash_alias_keeps_scope_queries_without_rewriting_signed_url(self):
        page = PAGE | {'url': 'https://example.org/article', 'final_url': 'https://example.org/article/'}
        bundle = {'request': {}, 'pages': {'https://example.org/article': page},
            'searches': [{'query': 'diesel export', 'results': [{'url': 'https://example.org/article/', 'title': 'Documents'}]}]}
        child = {'url': 'https://example.org/file.pdf?signature=exact&part=2', 'origin': 'observed_document_detail',
                 'parent_url': page['url'], 'depth': 1, 'parent_source_sha256': 'fixture'}
        with patch('ForecastAgent.market_repricing.source_profile.applicability', return_value={'automatic_followup_requires_scope_review': True}) as scope, patch(
                'ForecastAgent.market_repricing.source_profile.observed_details', return_value=[child]):
            lead = next(row for row in candidates(bundle) if row['url'] == child['url'])
        self.assertEqual(scope.call_args.args[2]['search_queries'], ['diesel export'])
        self.assertFalse(lead['automatic_capture_eligible'])
        self.assertEqual(lead['url'], 'https://example.org/file.pdf?signature=exact&part=2')


if __name__ == '__main__':
    unittest.main()
