"""Recorded failure regressions: no real model, search or page requests."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.readers.encoding import decode_response
from ForecastAgent.readers.loader import load_response
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.parser_repair import repair_compressed_pages
from ForecastAgent.runtime.contracts import ContractError
from ForecastAgent.runtime.contracts import validate
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.runtime.context import collection_context
from ForecastAgent.runtime import progress
from ForecastAgent.providers.archive import archive_lookup
from ForecastAgent.debug_trial import run_one

URL='https://example.org/report'
REQUEST={'question':'Will Bitcoin trade above $120,000?', 'resolution_criteria':'BTC USD before August 1, 2026',
    'as_of_utc':'2026-06-25T00:00:00Z', 'pipeline':'collection', 'acquisition_profile':'collection_v3'}


def compressed_page():
    raw=gzip.compress(b'<html><p>Atlas Windows release is coming soon. This is a dated product update.</p></html>')
    return {'url':URL,'final_url':URL,'content_type':'text/html','raw_response_base64':base64.b64encode(raw).decode(),
        'sha256':hashlib.sha256(raw).hexdigest(),'content':raw.decode('utf-8',errors='replace'),
        'retrieved_at_utc':'2026-10-01T00:00:00Z','archive_timestamp':'2026-04-01T00:00:00Z',
        'temporal_status':'archive_pre_cutoff_capture','capture_method':'wayback_replay','parser_version':'document_reader_v3',
        'documents':[], 'archive_provenance':{'replay_url':'https://web.archive.org/web/20260401000000id_/'+URL}}


class SevereCollectionRepairs(TestCase):
    def test_audit_only_document_urls_are_not_selectable_or_readable(self):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST)
            saved=compressed_page();task.bundle['pages'][URL]=saved;repair_compressed_pages(task)
            blocked=URL+'/current'
            task.bundle['pages'][blocked]={**task.bundle['pages'][URL], 'url':blocked,'temporal_status':'current_capture_possible_later_edits'}
            task.bundle['plan']=[{'id':'n','priority':'critical'}]
            tools=active_tools(task,COLLECTION_TOOLS)
            reader=next(t for t in tools if t['function']['name']=='read_document')
            self.assertEqual(reader['function']['parameters']['properties']['url']['enum'],[URL])
            before=task.budget()
            with self.assertRaises(ContractError) as error:
                validate(task,'read_document',{'url':blocked},tools)
            self.assertEqual(error.exception.details['code'],'invalid_choice')
            self.assertTrue(task.execute('read_document',{'url':blocked},'')['blocked'])
            self.assertEqual(before,task.budget())
            self.assertIn('coming soon',task.execute('read_document',{'url':URL},'')['content'])
    def test_single_case_resume_protects_other_tasks_and_consumed_prefix(self):
        import json
        with TemporaryDirectory() as temp:
            root=Path(temp);(root/'batch.json').write_text(json.dumps({'tasks':{'1':{},'2':{}}}))
            for qid in ('1','2'):
                directory=root/'tasks'/qid;directory.mkdir(parents=True)
                task=RetrievalTask(directory,{**REQUEST,'id':qid});task.bundle['result']={'incomplete':True}
                task.bundle['searches']=[{'status':'completed','results':[]}];task.save()
            protected=(root/'tasks/2/bundle.json').read_bytes()
            def resumed(request,directory,*keys):
                p=directory/'bundle.json';b=json.loads(p.read_text());b['result']={'incomplete':False}
                b['model_attempts']=[{'id':1,'status':'received','sha256':'new'}]
                p.write_text(json.dumps(b));return b
            with patch('ForecastAgent.debug_trial.run_retrieval',side_effect=resumed):
                summary=run_one(root,'1','tavily','ultra')
            self.assertEqual(summary['model_http_increment'],1)
            self.assertEqual(summary['tavily_increment'],0)
            self.assertEqual((root/'tasks/2/bundle.json').read_bytes(),protected)
            with self.assertRaises(ValueError):run_one(root,'1','tavily','ultra')

    def test_single_case_detects_modification_of_unselected_ledger(self):
        import json
        with TemporaryDirectory() as temp:
            root=Path(temp);(root/'batch.json').write_text(json.dumps({'tasks':{'1':{},'2':{}}}))
            for qid in ('1','2'):
                directory=root/'tasks'/qid;directory.mkdir(parents=True)
                task=RetrievalTask(directory,{**REQUEST,'id':qid});task.bundle['result']={'incomplete':True};task.save()
            def tamper(request,directory,*keys):
                (root/'tasks/2/bundle.json').write_text('{}')
                return __import__('json').loads((directory/'bundle.json').read_text())
            with patch('ForecastAgent.debug_trial.run_retrieval',side_effect=tamper),self.assertRaises(RuntimeError):
                run_one(root,'1','tavily','ultra')
    def test_gzip_html_preserves_original_hash_and_table(self):
        raw=gzip.compress(b'<p>Past champions</p><table><tr><td>Brazil</td><td>5</td></tr></table>')
        p=load_response({'url':URL,'final_url':URL,'content_type':'text/html','raw':raw},retrieved_at='2026-04-01T00:00:00Z')
        self.assertIn('Brazil',p['content'])
        self.assertTrue(p['body_diagnostics']['usable_text'])
        self.assertEqual(base64.b64decode(p['raw_response_base64']),raw)
        self.assertEqual(p['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertGreater(p['body_diagnostics']['table_count'],0)

    def test_corrupt_text_is_not_readable_or_progress(self):
        self.assertFalse(body_diagnostics('\ufffd\x00\x01'*100)['usable_text'])
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST);p=compressed_page()
            task.bundle['pages'][URL]=p
            self.assertTrue(task.page_view(p)['blocked'])
            self.assertEqual(progress.snapshot(task)['bodies'],set())

    def test_expansion_and_truncated_gzip_are_explicit_failures(self):
        with self.assertRaises(ValueError): decode_response(gzip.compress(b'x'*5000),maximum=100)
        with self.assertRaises((EOFError,OSError)):decode_response(gzip.compress(b'<p>body</p>')[:-8])

    @patch('socket.socket.connect',side_effect=AssertionError('Network forbidden'))
    def test_saved_repair_keeps_budgets_history_dates_and_is_idempotent(self, network):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST);p=compressed_page()
            task.bundle['pages'][URL]=p;task.bundle['searches']=[{'status':'completed','results':[]}]*3
            task.bundle['exa_searches']=[{'status':'completed','results':[]}]
            before=task.budget()
            self.assertEqual(repair_compressed_pages(task),[URL])
            q=task.bundle['pages'][URL]
            self.assertIn('coming soon',q['content'])
            self.assertEqual(q['archive_timestamp'],p['archive_timestamp'])
            self.assertEqual(q['sha256'],p['sha256'])
            self.assertEqual(task.bundle['page_history'][URL],[p])
            self.assertEqual(task.budget(),before)
            self.assertEqual(repair_compressed_pages(task),[])
            self.assertEqual(task.page_view(q)['read_url'],URL)
            network.assert_not_called()

    def test_future_btc_observation_plan_rejected_before_freeze(self):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST)
            needs=[{'id':'prices','condition':'Bitcoin USD price data from June 25, 2026 through July 31, 2026 to determine if it exceeded $120,000',
                'priority':'critical','expected_source':'Exchange API','query':'Bitcoin USD prices'}]
            before=task.budget()
            with self.assertRaises(ContractError):task.execute('plan_evidence',{'needs':needs},'')
            self.assertIsNone(task.bundle['plan']);self.assertEqual(before,task.budget())
            needs[0]['condition']='Bitcoin USD price data through June 24, 2026'
            task.execute('plan_evidence',{'needs':needs},'')
            self.assertIsNotNone(task.bundle['plan'])

    def test_completed_exa_is_explicit_in_compact_context(self):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST)
            task.bundle['exa_searches']=[{'status':'completed','results':[]}]
            projected=collection_context(task)
            self.assertIn('attempted_completed',projected[1]['content'])
            self.assertIn('provider_attempts',projected[1]['content'])

    def test_archive_slash_difference_is_allowed_but_query_and_host_are_not(self):
        request='https://example.org/sst?dm_id=natlan'
        original='https://example.org/sst/?dm_id=natlan'
        calls=[]
        def fetch(url):
            calls.append(url)
            if '/cdx/' in url:
                import json
                raw=json.dumps([['timestamp','original'],['20260228074022',original]]).encode()
                return {'raw_response_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}
            return {'final_url':url, 'content':'Data', 'sha256':'raw'}
        result=archive_lookup(request,datetime(2026,3,6,tzinfo=timezone.utc),fetch)
        self.assertEqual(len(calls),2);self.assertEqual(result['url'],request)
        self.assertEqual(result['archive_provenance']['original_url'],original)
        for bad in ('https://other.org/sst/?dm_id=natlan','https://example.org/sst/?dm_id=global'):
            original=bad;calls.clear()
            with self.assertRaises(ValueError):archive_lookup(request,datetime(2026,3,6,tzinfo=timezone.utc),fetch)
            self.assertEqual(len(calls),1)
