"""Source identity, frontier and observable data coverage are independent contracts."""
import unittest
from ForecastAgent.evidence.source_identity import observed_urls, transport_key, alias_family, inventory
from ForecastAgent.evidence.source_coverage import observe
from ForecastAgent.supplement import enhanced, frontier


class SourceArchitectureTests(unittest.TestCase):
    def test_observed_url_balances_markup_and_preserves_query(self):
        text='[source](https://example.org/default.htm).&nbsp; [math](https://example.org/Foo_(math))'
        self.assertEqual(observed_urls(text), ['https://example.org/default.htm','https://example.org/Foo_(math)'])
        url='https://example.org/data?date=2026-09-01&station=99'
        self.assertEqual(observed_urls(url.replace('&','&amp;')), [url])
        self.assertEqual(transport_key(url),url)
        self.assertNotEqual(alias_family(url),alias_family(url.replace('09-01','09-02')))
        self.assertNotEqual(transport_key('https://example.org/page/'),transport_key('https://example.org/page'))

    def test_body_duplicates_do_not_delete_originals(self):
        pages={'https://example.org/page':{'content':'Original body'},
               'https://example.org/page/':{'content':'Original body'}}
        result=inventory(pages)
        self.assertEqual(result['unique_body_count'],1)
        self.assertEqual(len(pages),2)
        self.assertFalse(result['resource_equivalence_verified'])

    def test_data_diagnostics_are_format_independent_and_conservative(self):
        question={'question':'What is the observation on September 1, 2026?',
                  'resolution_criteria':'Use station identifier 87654321 in centimetres at 08:00 CEST.'}
        body='01.09.2026\n87654321\ncm\n07:45#17\n08:00#18\n08:15#19'
        report=observe(question,'https://example.org/data.txt',body)
        self.assertTrue(report['data_capture_candidate'])
        self.assertFalse(report['clock']['timezone_verified'])
        self.assertFalse(observe(question,'https://example.org/data.txt',body.replace('87654321','187654321'))['data_capture_candidate'])
        self.assertFalse(observe(question,'https://example.org/data.txt',body.replace('01.09.2026','02.09.2026'))['data_capture_candidate'])
        self.assertFalse(observe(question,'https://example.org/2026-09-01/data.txt',body.replace('01.09.2026','02.09.2026'))['data_capture_candidate'])

    def test_frontier_keeps_rule_sources_but_defers_side_links(self):
        question={'question':'Will Jane Smith remain director?', 'resolution_criteria':'Use https://example.org/board'}
        b={'request':question,'pages':{'https://example.org/board':{'content':'Jane Smith remains director. '*8,
              'links':[{'url':f'https://example.org/jane-smith-{n}','text':'Jane Smith director details'} for n in range(10)]+
                      [{'url':'https://example.org/faqs.htm','text':'Jane Smith director FAQs'},
                       {'url':'https://example.org/john-brown','text':'John Brown director'}]}},
           'searches':[],'exa_searches':[],'source_leads':{}}
        candidates=enhanced.plan(b)['sources']
        routed=frontier.admit(candidates,question,pages=b['pages'])
        self.assertIn('https://example.org/board',[s['url'] for s in routed['accepted']])
        self.assertEqual(sum(s['origin']=='saved_link' for s in routed['accepted']),4)
        reasons={s['deferred_reason'] for s in routed['deferred']}
        self.assertIn('parent_branch_ceiling',reasons)
        self.assertIn('navigation_route',reasons)

    def test_page_leads_cannot_be_promoted_to_seed_sources(self):
        b={'request':{'question':'Will Jane Smith remain director?','resolution_criteria':'Use official records'},
           'pages':{},'searches':[],'exa_searches':[], 'source_leads':{
               'x':{'url':'https://example.org/jane-smith','title':'Jane Smith director',
                    'origin':'page_link','parent_url':'https://example.org/board'}}}
        row=enhanced.plan(b)['sources'][0]
        self.assertEqual(row['origin'],'saved_link')
        self.assertEqual(row['parent_url'],'https://example.org/board')

    def test_rule_alias_and_parameterized_resources_remain_distinct(self):
        q={'question':'Will Jane Smith remain director?',
           'resolution_criteria':'Use https://example.org/data?date=2026-09-01'}
        b={'request':q,'pages':{},'searches':[{'results':[
              {'url':'https://example.org/article','title':'Jane Smith director'},
              {'url':'https://example.org/article/','title':'Jane Smith director'},
              {'url':'https://example.org/data?date=2026-09-02','title':'Jane Smith director'}]}],
           'exa_searches':[],'source_leads':{}}
        report=frontier.admit(enhanced.plan(b)['sources'],q)
        urls=[s['url'] for s in report['accepted']]
        self.assertIn('https://example.org/data?date=2026-09-01',urls)
        self.assertIn('https://example.org/data?date=2026-09-02',urls)
        self.assertEqual(sum('/article' in u for u in urls),1)
        self.assertTrue(any(r['deferred_reason']=='possible_url_alias_or_already_queued' for r in report['deferred']))

    def test_url_period_is_a_routing_hint_not_body_coverage(self):
        from ForecastAgent.supplement.source_contract import contract, match_source
        q={'question':'What will Example Company report in Q2 2026?'}
        observation=match_source(contract(q),'https://example.org/Q2-2026.pdf','Example Company Q1 2026')
        self.assertEqual(observation['quarter_status'],'observed')
        self.assertFalse(observation['quarter_observed'])
        self.assertEqual(observation['body_quarters'],['1'])


if __name__=='__main__':unittest.main()
