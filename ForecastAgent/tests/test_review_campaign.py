"""The resume comparison must retain past costs and detect ledger replacement."""
from copy import deepcopy
from unittest import TestCase
from ForecastAgent.review_campaign import compare


def row(calls, tokens, searches, hashes):
    return {'question_id':'1','model_http_attempts':calls,'known_total_tokens':tokens,
        'search_attempts':searches,'exa_search_attempts':0,'transport_records_verified':calls,
        'transport_records':[{'raw_sha256':s} for s in hashes],
        'search_payload_sha256':['search-original'][:searches],'frozen_request_hash':'frozen-question',
        'limits':{'tavily_basic':3,'exa_search':1},'result':{'acquisition_complete':False},'acceptance':{}}


class CampaignReviewTests(TestCase):
    def test_incremental_cost_does_not_replace_cumulative_cost(self):
        prior=row(2,1000,1,['call-1','call-2'])
        current=row(3,1250,1,['call-1','call-2','call-3'])
        current['exa_search_attempts']=1
        comparisons,totals=compare([prior],[prior],[current])
        self.assertEqual(totals['resume_increment']['model_http_attempts'],1)
        self.assertEqual(totals['resume_increment']['known_total_tokens'],250)
        self.assertEqual(totals['cumulative']['known_total_tokens'],1250)
        self.assertEqual(totals['resume_increment']['search_attempts'],0)
        self.assertTrue(all(comparisons[0]['ledger_checks'].values()))

    def test_replaced_transport_and_search_prefixes_are_reported(self):
        prior=row(2,1000,1,['call-1','call-2'])
        current=deepcopy(prior)
        current['transport_records'][0]['raw_sha256']='replaced-call'
        current['search_payload_sha256'][0]='replaced-search'
        comparisons,_=compare([prior],[prior],[current])
        self.assertFalse(comparisons[0]['ledger_checks']['model_transport_prefix_unchanged'])
        self.assertFalse(comparisons[0]['ledger_checks']['tavily_search_prefix_unchanged'])
