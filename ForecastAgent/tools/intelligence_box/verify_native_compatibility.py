"""Run only in an isolated latest-host overlay, with fake transport."""
import json
import tempfile
import unittest
from unittest.mock import patch

class NativeCompatibilityTests(unittest.TestCase):
    def test_five_new_sources_share_native_requests_and_replay_after_restore(self):
        from ForecastAgent.tests.test_native_capabilities import task
        from ForecastAgent.runtime.retrieval import RetrievalTask
        from ForecastAgent.tools.intelligence_box import core
        from ForecastAgent.tools.intelligence_box.catalog import SOURCES
        from ForecastAgent.tavily_research import canonical_url
        cases=[('gdelt_news',{'query':'Tesla','maxrecords':1}),('dbnomics_series',{'provider':'INSEE','dataset':'TEST','series':'A.X'}),('bls_series',{'series':'CUUR0000SA0'}),('govinfo_feed',{'collection':'PLAW'}),('govinfo_text',{'package':'PLAW-119publ21'})]
        def transport(url,cap,**kwargs):
            kind='application/json'
            if 'gdeltproject.org' in url: body={'articles':[{'url':'https://example.org/news','title':'News lead'}]}
            elif 'db.nomics.world' in url: body={'series':{'docs':[{'provider_code':'INSEE','dataset_code':'TEST','series_code':'A.X','period':['2025'],'value':[1]}],'num_found':1}}
            elif 'api.bls.gov' in url: body={'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':'CUUR0000SA0','data':[{'year':'2025','period':'M13','value':'100'}]}]}}
            elif '/rss/' in url:
                return dict(raw=b'<rss><channel><item><title>Law</title><link>https://www.govinfo.gov/app/details/PLAW-119publ21</link></item></channel></rss>',status=200,content_type='application/xml',final_url=url)
            else:
                return dict(raw=b'<html><h1>Public law</h1><p>'+b'Original official statutory text. '*20+b'</p></html>',status=200,content_type='text/html',final_url=url)
            return dict(raw=json.dumps(body).encode(),status=200,content_type=kind,final_url=url)
        with tempfile.TemporaryDirectory() as root, patch.object(core,'transport',side_effect=transport) as http:
            t=task(root); results=[]
            for source,params in cases:
                c=t.execute('intelligence_fetch',{'source_id':source,'parameters':params,'need_ids':['revenue']},'')
                self.assertEqual(c['status'],'usable',c.get('error')); results.append(c)
                self.assertEqual(c['budget_authority'],'native_fetch_attempts')
            self.assertEqual(len(t.bundle['fetch_attempts']),5)
            self.assertEqual(http.call_count,5)
            self.assertFalse(results[0]['quality']['full_article_body'])
            restored=RetrievalTask(t.directory,t.bundle['request'])
            for source,params in cases:
                c=restored.execute('intelligence_fetch',{'source_id':source,'parameters':params,'need_ids':['revenue']},'')
                self.assertTrue(c['cached'])
            self.assertEqual(http.call_count,5)
            self.assertEqual(len(restored.bundle['fetch_attempts']),5)
            self.assertTrue(all(c['id'] in restored.bundle['channel_tools']['captures'] for c in results))

    def test_european_statistics_share_native_requests(self):
        from ForecastAgent.tests.test_native_capabilities import task
        from ForecastAgent.tools.intelligence_box import core
        cases=[('eurostat_data',{'dataset':'test','filters':'{"geo":"DE","time":"2025"}'}),('ecb_series',{'flow':'EXR','series':'M.USD.EUR.SP00.A','lastNObservations':2})]
        def wire(url,cap,**kwargs):
            if 'ecb.europa.eu' in url:
                raw=b'KEY,TIME_PERIOD,OBS_VALUE,UNIT\nEXR.M.USD.EUR.SP00.A,2025-01,1.1,USD\n';kind='text/csv'
            else:
                raw=json.dumps({'class':'dataset','id':['geo','time'],'size':[1,1],'dimension':{'geo':{'category':{'index':{'DE':0}}},'time':{'category':{'index':{'2025':0}}}},'value':{'0':1}}).encode();kind='application/json'
            return dict(raw=raw,status=200,content_type=kind,final_url=url)
        with tempfile.TemporaryDirectory() as root, patch.object(core,'transport',side_effect=wire) as http:
            t=task(root)
            for name,params in cases:
                c=t.execute('intelligence_fetch',{'source_id':name,'parameters':params,'need_ids':['revenue']},'')
                self.assertEqual(c['status'],'usable',c.get('error'))
                self.assertEqual(c['budget_authority'],'native_fetch_attempts')
            self.assertEqual(http.call_count,2);self.assertEqual(len(t.bundle['fetch_attempts']),2)

    def test_unknown_extensions_are_not_offered_as_free_local_tools(self):
        from ForecastAgent.tools import capabilities
        offered=capabilities.registry()
        for name in ('intelligence_discover','intelligence_acquire_link'):
            self.assertNotIn(name,offered)
        self.assertIn('network',offered['intelligence_fetch'].effects)

    def test_rate_limit_is_preserved_and_replayed_without_hidden_retry(self):
        from ForecastAgent.tests.test_native_capabilities import task
        from ForecastAgent.tools.intelligence_box import core
        with tempfile.TemporaryDirectory() as root, patch.object(core,'transport',return_value=dict(raw=b'rate limited',status=429,content_type='text/plain',final_url='https://api.gdeltproject.org/api/v2/doc/doc',retry_after='60')) as http:
            t=task(root); args={'source_id':'gdelt_news','parameters':{'query':'Tesla'},'need_ids':['revenue']}
            first=t.execute('intelligence_fetch',args,''); second=t.execute('intelligence_fetch',args,'')
            self.assertEqual(first['status'],'failed'); self.assertTrue(second['cached'])
            self.assertEqual(http.call_count,1); self.assertEqual(len(t.bundle['fetch_attempts']),1)
            self.assertEqual(first['http']['retry_after'],'60')

    def test_native_cap_blocks_transport_before_any_extra_request(self):
        from ForecastAgent.tests.test_native_capabilities import task
        from ForecastAgent.tools.intelligence_box import core
        with tempfile.TemporaryDirectory() as root, patch.object(core,'transport',side_effect=AssertionError('Native cap bypass')):
            t=task(root)
            t.bundle['fetch_attempts']=[{'status':'completed'} for _ in range(t.fetch_limit)]
            t.save()
            c=t.execute('intelligence_fetch',{'source_id':'bls_series','parameters':{'series':'CUUR0000SA0'},'need_ids':['revenue']},'')
            self.assertEqual(c['status'],'failed')
            self.assertEqual(len(t.bundle['fetch_attempts']),t.fetch_limit)

if __name__=='__main__': unittest.main()
