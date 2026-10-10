"""Native identity, missing values and budget semantics of no-key channels."""
import json
import tempfile
import unittest
from urllib.parse import urlsplit,parse_qs
from ForecastAgent.tools.intelligence_box.catalog import SOURCES
from ForecastAgent.tools.intelligence_box.core import Toolbox,build_url,parse

class PublicChannelsTests(unittest.TestCase):
    def decoded(self,name,body,params):
        return parse(dict(SOURCES[name],request_parameters=params),json.dumps(body).encode())

    def test_gdelt_bounds_remove_relative_default(self):
        url=build_url('gdelt_news',{'query':'"Tesla"','startdatetime':'20260901000000','enddatetime':'20260902000000'})
        query=parse_qs(urlsplit(url).query)
        self.assertNotIn('timespan',query)
        self.assertEqual(query['mode'],['artlist'])

    def test_invalid_params_rejected_without_spending(self):
        with tempfile.TemporaryDirectory() as root:
            box=Toolbox(root,max_requests=1,fetcher=lambda *_: self.fail('Unexpected HTTP'))
            try:
                cases=[('gdelt_news',{}),('gdelt_news',{'query':'x','maxrecords':True}),
                       ('gdelt_news',{'query':'x','startdatetime':'20260230000000','enddatetime':'20260301000000'}),
                       ('dbnomics_series',{'provider':'IMF','dataset':'WEO:latest','series':'x'}),
                       ('bls_series',{'series':'../x'}),('govinfo_feed',{'collection':'other'}),
                       ('govinfo_text',{'package':'PLAW-../x'})]
                for name,p in cases:
                    with self.assertRaises(ValueError): box.fetch(name,p)
                self.assertEqual(box.budget()['reserved_attempts'],0)
            finally: box.close()

    def test_gdelt_leads_do_not_claim_full_body_or_complete_window(self):
        records,coverage=self.decoded('gdelt_news',{'articles':[{'url':'https://example.org/a','title':'Title','seendate':'20261010T000000Z'}]}, {'maxrecords':1})
        self.assertEqual(len(records),1)
        self.assertFalse(coverage['native_metadata']['full_article_body'])
        self.assertIsNone(coverage['more_available'])
        self.assertTrue(coverage['native_metadata']['result_window_may_be_saturated'])

    def test_bls_success_with_wrong_identity_rejected(self):
        body={'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':'OTHER','data':[]}]}}
        with self.assertRaises(ValueError): self.decoded('bls_series',body,{'series':'CUUR0000SA0'})

    def test_bls_failure_envelope_rejected(self):
        with self.assertRaises(ValueError): self.decoded('bls_series',{'status':'REQUEST_NOT_PROCESSED','message':['quota']},{'series':'CUUR0000SA0'})

    def test_bls_annual_and_footnotes_retained(self):
        row={'year':'2025','period':'M13','value':'100.1','footnotes':[{'code':'P','text':'Preliminary'}]}
        for envelope in ({'series':[{'seriesID':'CUUR0000SA0','data':[row]}]},[{'series':[{'seriesID':'CUUR0000SA0','data':[row]}]}]):
            records,_=self.decoded('bls_series',{'status':'REQUEST_SUCCEEDED','Results':envelope},{'series':'CUUR0000SA0'})
            self.assertEqual(records[0]['period'],'M13')
            self.assertEqual(records[0]['footnotes'],row['footnotes'])
            self.assertEqual(records[0]['value'],'100.1')

    def dbdoc(self):
        return {'provider_code':'INSEE','dataset_code':'TEST','series_code':'A.X','period':['2024','2025'],'value':[1,'NA'],'period_start_day':['2024-01-01','2025-01-01'],'dimensions':{'unit':'INDEX'}}

    def test_dbnomics_native_missing_and_dimensions_retained(self):
        rows,_=self.decoded('dbnomics_series',{'series':{'docs':[self.dbdoc()],'num_found':1}},{'provider':'INSEE','dataset':'TEST','series':'A.X'})
        self.assertEqual(rows[1]['value'],'NA')
        self.assertEqual(rows[0]['_source_context']['dimensions'],{'unit':'INDEX'})
        self.assertNotIn('published_at',rows[0])

    def test_dbnomics_misaligned_arrays_rejected(self):
        doc=self.dbdoc(); doc['value']=[1]
        with self.assertRaises(ValueError): self.decoded('dbnomics_series',{'series':{'docs':[doc]}},{'provider':'INSEE','dataset':'TEST','series':'A.X'})

    def test_dbnomics_identity_mismatch_rejected(self):
        with self.assertRaises(ValueError): self.decoded('dbnomics_series',{'series':{'docs':[self.dbdoc()]}},{'provider':'OTHER','dataset':'TEST','series':'A.X'})

    def test_empty_dbnomics_is_valid_empty(self):
        rows,_=self.decoded('dbnomics_series',{'series':{'docs':[],'num_found':0}},{'provider':'INSEE','dataset':'TEST','series':'A.X'})
        self.assertEqual(rows,[])

    def test_bls_failure_and_cache_use_existing_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            raw=json.dumps({'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':'CUUR0000SA0','data':[]}]}}).encode()
            box=Toolbox(root,max_requests=1,fetcher=lambda url,cap:dict(raw=raw,status=200,final_url=url,content_type='application/json'))
            try:
                first=box.fetch('bls_series',{'series':'CUUR0000SA0'})
                second=box.fetch('bls_series',{'series':'CUUR0000SA0'})
                self.assertEqual(first['status'],'empty'); self.assertTrue(second['cache_hit'])
                self.assertFalse(first['quality']['historical_vintage_verified'])
                self.assertEqual(box.budget()['reserved_attempts'],1)
            finally: box.close()

    def test_govinfo_feed_uses_official_lowercase_path(self):
        self.assertEqual(build_url('govinfo_feed',{'collection':'PLAW'}),'https://www.govinfo.gov/rss/plaw.xml')

    def test_govinfo_rss_preserves_original_links_and_rejects_external_hosts(self):
        from ForecastAgent.tools.intelligence_box.discovery import parse_discovery
        source={'final_url':'https://www.govinfo.gov/rss/plaw.xml','discovery_parameters':{'kind':'rss','offset':0,'limit':10},'discovery_hosts':['www.govinfo.gov']}
        raw=b'<rss><channel><item><guid>PLAW-119publ21</guid><link>https://www.govinfo.gov/app/details/PLAW-119publ21</link><description><![CDATA[<a href="https://www.govinfo.gov/content/pkg/PLAW-119publ21/html/PLAW-119publ21.htm">TEXT</a><a href="https://evil.example/content/pkg/PLAW-119publ21/pdf/a.pdf">PDF</a>]]></description></item></channel></rss>'
        rows,_=parse_discovery(source,raw)
        self.assertEqual(rows[1]['link_role'],'original_rendition')
        self.assertTrue(rows[1]['eligible'])
        self.assertFalse(rows[2]['eligible'])
        self.assertEqual(rows[1]['guid'],'PLAW-119publ21')

    def test_govinfo_exact_package_url(self):
        self.assertEqual(build_url('govinfo_text',{'package':'PLAW-119publ21'}),'https://www.govinfo.gov/content/pkg/PLAW-119publ21/html/PLAW-119publ21.htm')

if __name__=='__main__': unittest.main()
