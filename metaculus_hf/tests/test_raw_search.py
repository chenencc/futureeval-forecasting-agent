import base64,hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import requests
from metaculus_agent.raw_search import installed,saved,validate
from metaculus_agent.products import context,product,bundle
from metaculus_agent.runner import initial_state
from metaculus_agent.storage import QuestionBatch,RuntimeCheckpoint,dumps
from pipeline_v2.providers import Transport
from pipeline_v2.acquisition import LiveTools
from pipeline_v2.contracts import PipelineError
from test_runner import MemoryStore,post,sealed

class Budget:
    def __init__(self):self.calls=[]
    def reserve(self,*a,**kw):self.calls.append((a,kw))

class Response:
    def __init__(self,body,status=200,url='https://api.exa.ai/search'):
        self.body,self.status_code,self.url=body,status,url;self.ok=status<400
        self.headers={'Content-Type':'application/json','X-Request-ID':'fixture-id','Set-Cookie':'private-session'}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):
        for i in range(0,len(self.body),7):yield self.body[i:i+7]

class RawSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        env=patch.dict(os.environ,{'EXA_API':'fixture-exa-secret','TAVILY_KEY3':'fixture-tavily-first','TAVILY_KEY4':'fixture-tavily-backup'});env.start();self.addCleanup(env.stop)
        self.budget=Budget();self.transport=Transport(self.budget,'run-fixture');self.calls=[];self.responses=[]
        def send(session,request,**kwargs):
            self.calls.append((request,kwargs));value=self.responses.pop(0)
            if isinstance(value,Exception):raise value
            value.url=request.url;return value
        mock=patch('requests.Session.send',send);mock.start();self.addCleanup(mock.stop)
    def test_exa_full_original_bytes_keep_unknown_fields_and_leave_adapter_unchanged(self):
        raw=b'{ "requestId":"id", "results":[{"url":"https://example.org/a","title":"A","highlights":["body"],"newField":{"x":[1,2]}}], "future_field": {"all":"kept"} }\n'
        self.responses=[Response(raw)]
        with installed(self.root):normalized=LiveTools(self.transport,question_id='metaculus:100').exa_search('query')
        records=saved(self.root);self.assertEqual(len(records),1);r=records[0]
        self.assertEqual(base64.b64decode(r['body_base64']),raw);self.assertEqual(r['body_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertTrue(r['body_complete']);self.assertTrue(r['byte_exact']);self.assertEqual(r['http_status'],200)
        self.assertTrue(r['requested_at']);self.assertTrue(r['received_at']);self.assertNotIn('set-cookie',r['response_headers'])
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.budget.calls),1)
        self.assertNotIn('raw_search_responses',normalized);self.assertEqual(normalized['results'][0]['snippet'],'body')
        self.assertNotIn('fixture-exa-secret',json.dumps(r));self.assertEqual(r['request_params']['query'],'query')
    def test_tavily_full_error_and_backup_response_without_changing_failover(self):
        bad=b'{"error":"quota reached","unknown_error_details":{"count":7}}'
        good=b'{"request_id":"ok","results":[{"url":"https://example.org","content":"full snippet","unknown":42}],"extra_top":1}'
        self.responses=[Response(bad,429),Response(good)]
        with installed(self.root):result=LiveTools(self.transport).search('query')
        rows=saved(self.root);self.assertEqual([x['http_status'] for x in rows],[429,200])
        self.assertEqual([base64.b64decode(x['body_base64']) for x in rows],[bad,good])
        self.assertTrue(all(x['body_complete'] for x in rows));self.assertEqual(len(self.calls),2);self.assertEqual(len(self.budget.calls),2)
        self.assertEqual(result['results'][0]['snippet'],'full snippet');self.assertNotIn('api_key',json.dumps(rows))
        self.assertNotIn('fixture-tavily-first',json.dumps(rows));self.assertNotIn('fixture-tavily-backup',json.dumps(rows))
    def test_invalid_json_is_retained_before_json_parser_failure(self):
        raw=b'not json\x00\xff';self.responses=[Response(raw)]
        with installed(self.root),self.assertRaisesRegex(PipelineError,'INVALID_PROVIDER_JSON'):
            self.transport.json('search','POST','https://api.exa.ai/search',payload={'query':'q'})
        r=saved(self.root)[0];self.assertEqual(base64.b64decode(r['body_base64']),raw);self.assertTrue(r['byte_exact'])
    def test_echoed_credential_is_redacted_and_never_claimed_byte_exact(self):
        raw=b'{"error":"bad fixture-tavily-first"}';self.responses=[Response(raw,401)]
        with installed(self.root),self.assertRaises(PipelineError):
            self.transport.json('search','POST','https://api.tavily.com/search',payload={'query':'q','api_key':'fixture-tavily-first'})
        r=saved(self.root)[0];body=base64.b64decode(r['body_base64'])
        self.assertNotIn(b'fixture-tavily-first',body);self.assertIn(b'[REDACTED]',body)
        self.assertTrue(r['credential_redacted']);self.assertFalse(r['byte_exact']);self.assertTrue(r['body_complete'])
    def test_model_and_page_requests_are_not_archived(self):
        self.responses=[Response(b'{}'),Response(b'page')]
        with installed(self.root):
            self.transport.json('model','POST','https://openrouter.ai/api/v1/chat/completions',payload={})
            self.transport.request('fetch','GET','https://example.org/a')
        self.assertEqual(saved(self.root),[]);self.assertEqual(len(self.calls),2)
    def test_response_limit_marks_received_prefix_incomplete_without_extra_read(self):
        self.responses=[Response(b'0123456789'*30)]
        with installed(self.root),self.assertRaisesRegex(PipelineError,'RESPONSE_TOO_LARGE'):
            self.transport.request('search','POST','https://api.exa.ai/search',payload={'query':'q'},limit=10)
        r=saved(self.root)[0];self.assertFalse(r['body_complete']);self.assertFalse(r['byte_exact'])
        self.assertEqual(r['body_bytes'],14);self.assertEqual(len(self.calls),1)
    def test_connection_failure_is_explicit_and_no_body_is_fabricated(self):
        self.responses=[requests.ConnectionError('fixture failure')]
        with installed(self.root),self.assertRaisesRegex(PipelineError,'TRANSPORT_ERROR'):
            self.transport.json('search','POST','https://api.exa.ai/search',payload={'query':'q'})
        r=saved(self.root)[0];self.assertTrue(r['dispatched']);self.assertFalse(r['body_complete']);self.assertIsNone(r['http_status'])
        self.assertEqual(r['body_base64'],'');self.assertEqual(r['error_code'],'TRANSPORT_ERROR')
    def test_prior_local_attempts_are_preserved_on_resume(self):
        for body in (b'{"first":1}',b'{"second":2}'):
            self.responses=[Response(body)]
            with installed(self.root):self.transport.json('search','POST','https://api.exa.ai/search',payload={'query':'q'})
        self.assertEqual(len(saved(self.root)),2)
    def test_raw_body_corruption_is_rejected(self):
        self.responses=[Response(b'{"saved":1}')]
        with installed(self.root):self.transport.json('search','POST','https://api.exa.ai/search',payload={'query':'q'})
        rows=saved(self.root);rows[0]['body_base64']=base64.b64encode(b'{"changed":1}').decode()
        with self.assertRaisesRegex(ValueError,'RAW_SEARCH_BODY_HASH'):validate(rows)
    def test_raw_context_roundtrip_preserves_snapshot_and_existing_analysis_input(self):
        task=dict(id='100',post_id='1100',post=post(),run_id='fixture',started_at='2026-10-28T00:00:00Z',rule_identity='fixture',request={'question':'fixture','question_type':'binary'})
        package=sealed(task,self.root/'native');old=context(task,package,task['snapshot_id'])
        self.responses=[Response(b'{"unknown":true}')]
        with installed(self.root):self.transport.json('search','POST','https://api.exa.ai/search',payload={'query':'q'})
        new=context(task,package,task['snapshot_id'],raw_search_responses=saved(self.root))
        self.assertEqual({k:v for k,v in new.items() if k!='raw_search_responses'},old)
        self.assertEqual(new['snapshot_id'],old['snapshot_id'])
        a,b=bundle(old),bundle(new);a.pop('context_sha256');b.pop('context_sha256');self.assertEqual(a,b)
        import pyarrow as pa,pyarrow.parquet as pq
        raw=product('context',new);restored=json.loads(pq.read_table(pa.BufferReader(raw)).to_pylist()[0]['payload'])
        self.assertEqual(restored,new)
    def test_new_raw_file_is_included_in_only_final_question_batch(self):
        backend=MemoryStore();state=initial_state('fixture');key='metaculus/_control/checkpoint.json';backend.publish({key:dumps(state)})
        batch=QuestionBatch(backend,self.root/'stage');cp=RuntimeCheckpoint(batch,self.root/'runtime');state=cp.restore();head=backend.head
        root=cp.root/'tasks/100';self.responses=[Response(b'{"extra":"retained"}')]
        with installed(root):self.transport.json('search','POST','https://api.exa.ai/search',payload={'query':'q'})
        cp.save(state);self.assertEqual(backend.head,head);batch.flush();self.assertEqual(batch.successful_commits,1)
        cold=RuntimeCheckpoint(backend,self.root/'cold');cold.restore();rows=saved(cold.root/'tasks/100')
        self.assertEqual(base64.b64decode(rows[0]['body_base64']),b'{"extra":"retained"}')

if __name__=='__main__':unittest.main()
