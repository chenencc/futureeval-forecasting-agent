"""Agent dispatch, cross-channel recovery and evidence acceptance integration."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.tools.intelligence_box.core import Toolbox,TOOL_DEFINITIONS

HTML=b'<html><h1>Official report</h1><p>'+b'Published original financial facts and reporting context. '*12+b'</p><h2>USD millions</h2><table><tr><th>Metric</th><th>2026</th></tr><tr><td>Revenue</td><td>42</td></tr></table><a href="https://assets-ir.tesla.com/quarter.pdf">Quarter earnings report</a></html>'
CLML=b'<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"><P1 id="section-1"><Text>Original legal provision.</Text></P1></Legislation>'

class AgentToolboxTests(unittest.TestCase):
    def test_all_fourteen_declared_tools_execute_by_json_dispatch(self):
        touched=set(); calls=[]
        def fetch(url,limit):
            calls.append(url); kind='text/html'; raw=HTML
            request={'congress':'119','billType':'hr','billNumber':'1'}
            if '/text?' in url:
                raw=json.dumps({'textVersions':[{'type':'Enrolled','date':'2025-01-01','formats':[{'type':'Formatted Text','url':'https://www.govinfo.gov/content/pkg/BILLS-119hr1enr/html/BILLS-119hr1enr.htm'}]}],'request':request}).encode();kind='application/json'
            elif '/data.xml' in url: raw=CLML;kind='application/xml'
            elif url.endswith('index.xml'): raw=b'<urlset><url><loc>https://www.fec.gov/detail</loc></url></urlset>';kind='application/xml'
            return dict(raw=raw,status=200,final_url=url,content_type=kind)
        with tempfile.TemporaryDirectory() as root, patch('socket.create_connection',side_effect=AssertionError('Real network forbidden')):
            box=Toolbox(root,max_requests=7,fetcher=fetch,configuration={'CONGRESS_API_KEY':'fixture-not-a-real-key'})
            def invoke(name,args):
                touched.add(name); return box.call(name,json.loads(json.dumps(args)))
            try:
                catalog=invoke('intelligence_catalog',{'domain':'news'})
                self.assertEqual(catalog['sources'][0]['id'],'gdelt_news')
                invoke('intelligence_budget',{})
                c=invoke('intelligence_profile',{'profile_id':'tesla_ir'})
                invoke('intelligence_read',{'url':'https://www.fec.gov/other'})
                requests=box.budget()['reserved_attempts']
                self.assertTrue(invoke('intelligence_links',{'capture_id':c['id']})['links'])
                self.assertTrue(invoke('intelligence_tables',{'capture_id':c['id']})['tables'])
                invoke('intelligence_outline',{'capture_id':c['id']})
                hit=invoke('intelligence_search',{'capture_id':c['id'],'query':'Revenue'})['hits'][0]
                part=invoke('intelligence_part',{'capture_id':c['id'],'unit_id':hit['unit_id']})
                self.assertIn('Revenue',part['text'])
                self.assertEqual(box.budget()['reserved_attempts'],requests)
                law=invoke('intelligence_fetch',{'source_id':'uk_legislation_xml','parameters':{'type':'ukpga','year':2025,'number':1}})
                self.assertEqual(invoke('intelligence_provisions',{'capture_id':law['id'],'provision_id':'section-1'})['status'],'found')
                index=invoke('intelligence_fetch',{'source_id':'congress_texts','parameters':{'congress':119,'bill_type':'hr','bill_number':1}})
                self.assertEqual(invoke('intelligence_bill_text',{'capture_id':index['id'],'version_index':0})['status'],'usable')
                discovery=invoke('intelligence_discover',{'url':'https://www.fec.gov/index.xml','kind':'sitemap'})
                # The selected original is still a separately counted operation.
                self.assertEqual(invoke('intelligence_acquire_link',{'capture_id':discovery['id'],'index':0})['status'],'usable')
                with self.assertRaises(RuntimeError): box.read('https://www.fec.gov/extra')
                self.assertEqual(touched,{d['function']['name'] for d in TOOL_DEFINITIONS})
                self.assertEqual(len(calls),7)
            finally: box.close()

    def test_schema_errors_spend_no_requests(self):
        with tempfile.TemporaryDirectory() as root:
            box=Toolbox(root,max_requests=1,fetcher=lambda *_:self.fail('HTTP invoked'))
            try:
                cases=[('intelligence_read',{}),('intelligence_fetch',{'source_id':'unknown'}),('intelligence_catalog',{'domain':'unknown'}),('intelligence_budget',{'reset':True}),('intelligence_tables',{'capture_id':'x','max_rows':True}),('intelligence_search',{'capture_id':'x','query':'x'*201}),('intelligence_part',{'capture_id':'x','unit_id':'x','start':-1})]
                for name,args in cases:
                    with self.assertRaises(ValueError): box.call(name,args)
                self.assertEqual(box.budget()['reserved_attempts'],0)
            finally: box.close()

    def test_http_failure_cannot_be_extracted_as_links_or_tables(self):
        with tempfile.TemporaryDirectory() as root:
            box=Toolbox(root,max_requests=1,fetcher=lambda url,limit:dict(raw=HTML,status=403,content_type='text/html',final_url=url))
            try:
                c=box.call('intelligence_read',{'url':'https://www.fec.gov/detail'})
                for name in ('intelligence_links','intelligence_tables','intelligence_outline'):
                    with self.assertRaises(ValueError): box.call(name,{'capture_id':c['id']})
                self.assertEqual(box.budget()['reserved_attempts'],1)
            finally: box.close()

    def test_response_truncation_is_failure_not_success(self):
        with tempfile.TemporaryDirectory() as root:
            box=Toolbox(root,max_requests=1,fetcher=lambda url,limit:dict(raw=HTML,status=200,truncated=True,content_type='text/html',final_url=url))
            try:
                c=box.read('https://www.fec.gov/detail')
                self.assertEqual(c['status'],'failed')
                self.assertFalse(c['quality'].get('original_download_complete',False))
                with self.assertRaises(ValueError): box.tables(c['id'])
                self.assertTrue((Path(root)/c['raw_path']).exists())
            finally: box.close()

    def test_resume_preserves_cap_failure_and_cross_channel_budget(self):
        with tempfile.TemporaryDirectory() as root:
            fail=lambda url,limit:dict(raw=b'rate limited',status=429,content_type='text/plain',final_url=url,retry_after='60')
            box=Toolbox(root,max_requests=2,fetcher=fail)
            first=box.fetch('gdelt_news',{'query':'Tesla'})
            self.assertEqual(first['http']['retry_after'],'60'); box.close()
            box=Toolbox(root,max_requests=2,fetcher=fail)
            try:
                self.assertEqual(box.budget()['reserved_attempts'],1)
                box.fetch('bls_series',{'series':'CUUR0000SA0'})
                with self.assertRaises(RuntimeError): box.fetch('govinfo_feed',{'collection':'PLAW'})
                self.assertEqual(box.budget()['remaining'],0)
                self.assertEqual(json.loads((Path(root)/'captures'/first['id']/'capture.json').read_text())['status'],'failed')
            finally: box.close()

    def test_skill_contracts_and_examples_match_executable_tools(self):
        from ForecastAgent.tools.intelligence_box.contracts import validate_call
        root=Path(__file__).parent/'tools'/'intelligence_box'/'skills'/'intelligence-acquisition'/'references'
        self.assertEqual(json.loads((root/'tool-contracts.json').read_text()),TOOL_DEFINITIONS)
        calls=json.loads((root/'examples.json').read_text())['calls']
        for call in calls: validate_call(call['name'],call['arguments'],TOOL_DEFINITIONS)
        self.assertEqual({c['name'] for c in calls},{d['function']['name'] for d in TOOL_DEFINITIONS})

    def test_json_schemas_have_unique_names_and_current_sources(self):
        from ForecastAgent.tools.intelligence_box.catalog import SOURCES
        names=[d['function']['name'] for d in TOOL_DEFINITIONS]
        self.assertEqual(len(names),len(set(names)))
        definition=next(d['function'] for d in TOOL_DEFINITIONS if d['function']['name']=='intelligence_fetch')
        self.assertEqual(set(definition['parameters']['properties']['source_id']['enum']),set(SOURCES))

if __name__=='__main__': unittest.main()
