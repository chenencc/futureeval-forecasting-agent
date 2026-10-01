"""Internal orchestration vocabulary must not consume paid discovery calls."""
from unittest import TestCase
from unittest.mock import patch
from ForecastAgent.providers.tavily_search import search_batch, search_options
from ForecastAgent.providers.exa_search import search


class DiscoveryQueryTests(TestCase):
    @patch('ForecastAgent.providers.tavily_search.urlopen')
    @patch('ForecastAgent.providers.exa_search.urlopen')
    def test_internal_query_is_rejected_before_either_provider(self, exa, tavily):
        for query in ('exa_search','dated_observations','tavily_basic','search_exa','collect_dataset'):
            with self.subTest(query=query):
                with self.assertRaisesRegex(ValueError,'internal channel ID'):
                    search(query,'test-only-key')
                with self.assertRaisesRegex(ValueError,'internal channel ID'):
                    search_batch(query,'test-only-key')
        exa.assert_not_called();tavily.assert_not_called()

    def test_real_entity_queries_remain_allowed(self):
        for query in ('ChatGPT Atlas Windows official release','North Atlantic SST NOAA OISST daily records',
                      'OpenAI public SEC S-1 filing','FIFA World Cup 2026 qualified teams',
                      'Exa funding official announcement'):
            self.assertEqual(search_options(query)['topic'],'general')
