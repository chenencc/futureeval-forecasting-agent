"""Congress credential safety, bill identity and version provenance checks."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from email.message import Message
from ForecastAgent.tools.intelligence_box.core import Toolbox,build_url,transport
from ForecastAgent.tools.intelligence_box.congress import select_text
from ForecastAgent.tools.intelligence_box.catalog import catalog


class CongressTests(unittest.TestCase):
    params={'congress':119,'bill_type':'hr','bill_number':1}

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def box(self,body):
        def fetch(url,limit):
            return {'raw':json.dumps(body).encode(),'status':200,'final_url':url,'content_type':'application/json'}
        box=Toolbox(self.temp.name,max_requests=3,fetcher=fetch,configuration={'CONGRESS_API_KEY':'offline-fixture-secret'})
        self.addCleanup(box.close)
        return box

    def envelope(self,field,records,total=None):
        return {field:records,'request':{'congress':'119','billType':'hr','billNumber':'1'},
                'pagination':{'count':len(records) if total is None else total,'next':'https://api.congress.gov/v3/bill/119/hr/1/actions?offset=20' if total else None}}

    def test_missing_key_does_not_reserve(self):
        box=self.box({})
        box.configuration={}
        with patch.dict('os.environ',{'CONGRESS_API_KEY':''}):
            result=box.fetch('congress_bill',self.params)
        self.assertEqual(result['status'],'configuration_required')
        self.assertEqual(box.budget()['reserved_attempts'],0)

    def test_strict_bill_and_page_parameters(self):
        for changes in ({'bill_type':'HR'},{'congress':'../119'},{'bill_number':0},{'limit':251},{'offset':-1},{'limit':True}):
            with self.assertRaises(ValueError): build_url('congress_actions',dict(self.params,**changes))

    def test_detail_preserves_latest_action_and_law_without_inference(self):
        bill={'congress':119,'type':'HR','number':'1','introducedDate':'2025-05-20',
              'latestAction':{'actionDate':'2025-07-04','text':'Became Public Law No: 119-21.'},
              'laws':[{'type':'Public Law','number':'119-21'}]}
        result=self.box({'bill':bill}).fetch('congress_bill',self.params)
        self.assertEqual(result['records'][0],bill)
        self.assertFalse(result['source_binding']['enactment_inferred'])
        self.assertFalse(result['source_binding']['commencement_inferred'])

    def test_wrong_bill_is_rejected_but_raw_retained(self):
        result=self.box({'bill':{'congress':118,'type':'HR','number':'1'}}).fetch('congress_bill',self.params)
        self.assertEqual(result['status'],'failed')
        self.assertTrue((Path(self.temp.name)/result['raw_path']).exists())

    def test_actions_have_explicit_pagination_and_native_dates(self):
        action={'actionDate':'2025-01-01','text':'Introduced in House','sourceSystem':{'name':'House'}}
        result=self.box(self.envelope('actions',[action],3)).fetch('congress_actions',self.params)
        self.assertEqual(result['records'][0],action)
        self.assertTrue(result['coverage']['more_available'])
        self.assertEqual(result['coverage']['native_metadata']['pagination']['count'],3)

    def test_empty_versions_and_wrong_action_identity(self):
        box=self.box(self.envelope('textVersions',[]))
        self.assertEqual(box.fetch('congress_texts',self.params)['status'],'empty')
        body=self.envelope('actions',[])
        body['request']['billNumber']='2'
        box.fetcher=lambda url,limit:{'raw':json.dumps(body).encode(),'status':200,'final_url':url,'content_type':'application/json'}
        self.assertEqual(box.fetch('congress_actions',self.params)['status'],'failed')

    def test_reflected_key_is_not_saved(self):
        body=self.envelope('actions',[])
        body['request']['api_key']='offline-fixture-secret'
        result=self.box(body).fetch('congress_actions',self.params)
        self.assertTrue(result['raw_redacted'])
        for file in Path(self.temp.name).glob('captures/**/*'):
            if file.is_file(): self.assertNotIn(b'offline-fixture-secret',file.read_bytes())
        self.assertNotIn(b'offline-fixture-secret',(Path(self.temp.name)/'journal.sqlite').read_bytes())

    def test_auth_header_only_for_api_host(self):
        class Response:
            code=200
            headers=Message()
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'{}'
            def geturl(self): return 'https://api.congress.gov/v3/bill'
        with patch('ForecastAgent.tools.intelligence_box.core.public_url'), patch('ForecastAgent.tools.intelligence_box.core.build_opener') as opener:
            opener.return_value.open.return_value=Response()
            transport('https://api.congress.gov/v3/bill',100,api_key='offline-fixture-secret')
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('X-api-key'),'offline-fixture-secret')
            self.assertNotIn('offline-fixture-secret',request.full_url)
            with self.assertRaises(ValueError): transport('https://www.govinfo.gov/',100,api_key='offline-fixture-secret')

    def test_original_text_selection_keeps_version_and_parent_hash(self):
        version={'type':'Enrolled Bill','date':'2025-07-03',
                 'formats':[{'type':'Formatted Text','url':'https://www.congress.gov/119/bills/hr1/BILLS-119hr1enr.htm'}]}
        box=self.box(self.envelope('textVersions',[version]))
        index=box.fetch('congress_texts',self.params)
        body=b'<html><body><p>Enrolled bill legislative text section 1. An Act to provide the following provisions.</p></body></html>'
        box.fetcher=lambda url,limit:{'raw':body,'status':200,'final_url':url,'content_type':'text/html'}
        result=box.bill_text(index['id'],0)
        self.assertEqual(result['status'],'usable')
        self.assertEqual(result['source_binding']['version_type'],'Enrolled Bill')
        self.assertEqual(result['source_binding']['index_raw_sha256'],index['raw_sha256'])
        self.assertEqual(box.budget()['reserved_attempts'],2)
        self.assertFalse(result['source_binding']['enactment_inferred'])

    def test_original_link_mismatch_rejected_before_network(self):
        capture={'source_id':'congress_texts','status':'usable','source_binding':dict(self.params),
                 'records':[{'formats':[{'url':'https://www.congress.gov/119/bills/hr10/BILLS-119hr10enr.htm'}]}]}
        with self.assertRaises(ValueError): select_text(capture,0,0)
        with self.assertRaises(ValueError): select_text(capture,-1,0)

    def test_catalog_marks_keyed_sources(self):
        sources={s['id']:s for s in catalog()['sources']}
        self.assertTrue(sources['congress_bill']['requires_key'])
        self.assertFalse(sources['worldbank']['requires_key'])

    def test_public_law_requires_matching_saved_law_record(self):
        identity={'congress':119,'bill_type':'hr','bill_number':1}
        capture={'source_id':'congress_texts','status':'usable','id':'index','raw_sha256':'hash',
                 'source_binding':identity,'records':[{'type':'Public Law','formats':[{'url':'https://www.congress.gov/119/plaws/publ21/PLAW-119publ21.htm'}]}]}
        with self.assertRaises(ValueError): select_text(capture,0,0)
        detail={'source_id':'congress_bill','status':'usable','id':'detail','raw_sha256':'detailhash',
                'source_binding':identity,'records':[{'laws':[{'type':'Public Law','number':'119-21'}]}]}
        url,binding=select_text(capture,0,0,detail)
        self.assertEqual(binding['law_binding']['official_law_record']['number'],'119-21')
        detail['records'][0]['laws'][0]['number']='119-210'
        with self.assertRaises(ValueError): select_text(capture,0,0,detail)

    def test_final_page_distinguishes_prior_records_from_next_page(self):
        from ForecastAgent.tools.intelligence_box.core import parse
        from ForecastAgent.tools.intelligence_box.catalog import SOURCES
        body=self.envelope('actions',[{'text':'Introduced'}],59)
        body['pagination'].pop('next')
        records,coverage=parse(SOURCES['congress_actions'],json.dumps(body).encode())
        self.assertTrue(coverage['more_available'])
        self.assertFalse(coverage['next_page_available'])

    def test_parent_capture_change_does_not_reuse_wrong_text_binding(self):
        version={'type':'Enrolled Bill','formats':[{'url':'https://www.congress.gov/119/bills/hr1/BILLS-119hr1enr.htm'}]}
        box=self.box(self.envelope('textVersions',[version]))
        first=box.fetch('congress_texts',self.params)
        body=b'<html><body><p>Legislative text section 1. This act shall provide all of the following provisions.</p></body></html>'
        box.fetcher=lambda url,limit:{'raw':body,'status':200,'final_url':url,'content_type':'text/html'}
        original=box.bill_text(first['id'],0)
        altered=box._saved(first['id'])[0]
        altered['id']='different-index'
        with patch.object(box,'_saved',return_value=(altered,b'')):
            repeat=box.bill_text(first['id'],0)
        self.assertFalse(repeat['cache_hit'])
        self.assertEqual(repeat['source_binding']['index_capture_id'],'different-index')


if __name__=='__main__': unittest.main()
