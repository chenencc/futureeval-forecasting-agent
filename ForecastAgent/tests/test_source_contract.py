"""Source planning guards preserve recall without mistaking context for evidence."""
import unittest
from ForecastAgent.supplement.enhanced import assess, plan
from ForecastAgent.supplement.source_contract import contract, match_source


class SourceContractTests(unittest.TestCase):
    def test_unrelated_issuer_is_not_promoted(self):
        q = {'question': 'How much digital advertising revenues will the New York Times Company report in its Q2 2026 earnings release?',
             'resolution_criteria': 'Digital advertising reported for the second quarter.',
             'background': 'https://nytco-assets.nytimes.com/2026/05/Q1-2026-Earnings-Release.pdf'}
        page = 'Goldman Sachs Q2 2026 investor presentations and financial reporting. Advertising revenues and market research.'
        a = assess(q, 'https://www.goldmansachs.com/investor-relations', page)
        self.assertTrue(a['eligible_for_evidence'])
        self.assertEqual(a['coverage_status'], 'gap_or_context')
        self.assertEqual(a['source_contract']['entity_status'], 'not_observed')
        b = {'request': q, 'pages': {}, 'searches': [{'results': [
            {'url': 'https://www.goldmansachs.com/investor-relations', 'title': page},
            {'url': 'https://nytco-assets.nytimes.com/2026/08/Q2-2026-Earnings-Release.pdf', 'title': 'Q2 Earnings release'}]}]}
        self.assertEqual([r['url'] for r in plan(b)['sources']], ['https://nytco-assets.nytimes.com/2026/08/Q2-2026-Earnings-Release.pdf'])

    def test_month_and_generic_release_words_do_not_match_unrelated_topics(self):
        q = {'question': 'What will the housing starts be for July 2026?', 'resolution_criteria': 'Use Census first release.'}
        page = 'Hammer Museum press releases, July 2026 exhibition schedule. Annual exhibition releases.'
        self.assertEqual(assess(q, 'https://hammer.ucla.edu/press-room', page)['coverage_status'], 'gap_or_context')
        b = {'request': q, 'pages': {}, 'searches': [{'results': [{'url': 'https://hammer.ucla.edu/press-room', 'title': page}]}]}
        self.assertEqual(plan(b)['sources'], [])

    def test_rule_primary_retained_even_when_label_has_no_keywords(self):
        q = {'question': 'How many arrivals in June 2026?', 'resolution_criteria': 'Resolve using https://example.org/table/42.'}
        p = plan({'request': q, 'pages': {}})
        self.assertTrue(p['sources'][0]['rule_primary'])

    def test_wrong_quarter_is_context_and_release_is_not_verified(self):
        q = {'question': 'What will Example Company report in Q2 2026?', 'resolution_criteria': 'Use the value as initially published.'}
        expected = contract(q)
        observation = match_source(expected, 'https://example.org', 'Example Company Q3 2026 results')
        self.assertFalse(observation['quarter_observed'])
        self.assertFalse(observation['initial_release_verified'])


if __name__ == '__main__': unittest.main()
