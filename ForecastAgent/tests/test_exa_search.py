"""Exa budget durability, temporal isolation, metadata and provider boundaries."""
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError

from ForecastAgent.providers.exa_search import search, ExaError
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.evidence.intelligence import intelligence_package

REQUEST = {'id':'1','question':'Agency release','resolution_criteria':'Official release',
           'mode':'live','pipeline':'collection','acquisition_profile':'collection_v3'}
ARGS = {'query':'agency official release','need_ids':['n'],'reason':'Missing official source',
        'search_role':'official_gap','category':'general','include_domains':['example.org']}


class ExaTests(TestCase):
    @patch.dict(os.environ, {'EXA_API_KEY':'test-only-key'})
    @patch('ForecastAgent.runtime.retrieval.search_exa')
    def test_success_discovery_and_budget_persist_without_spending_tavily(self, provider):
        provider.return_value = {'results':[{'url':'https://example.org/release','published_date':'2026-02-01T00:00:00Z','provider':'exa'}],
            'raw_response':{'results':[]},'request_payload':{'numResults':10},'request_id':'req-1','cost_dollars_estimate':{'total':0.007}}
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), REQUEST)
            task.bundle['plan'] = [{'id':'n','priority':'critical'}]
            task.execute('search_exa', ARGS, '')
            restored = RetrievalTask(Path(directory), REQUEST)
            self.assertEqual(restored.budget()['exa_search_remaining'], 0)
            self.assertEqual(restored.budget()['tavily_basic_remaining'], 3)
            self.assertIn('https://example.org/release', restored.catalog())
            package = intelligence_package(restored.bundle)
            self.assertEqual(package['resources']['exa_search_attempts'],1)
            self.assertEqual(package['exa_searches'][0]['request_id'],'req-1')
            with self.assertRaises(ValueError):
                restored.execute('search_exa', ARGS, '')
            self.assertEqual(provider.call_count,1)

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only-key'})
    @patch('ForecastAgent.runtime.retrieval.search_exa', side_effect=ExaError(402,'Out of credits'))
    def test_failed_request_and_unknown_outcome_cannot_restart_budget(self, provider):
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), REQUEST)
            task.bundle['plan'] = [{'id':'n','priority':'critical'}]
            with self.assertRaises(RuntimeError):
                task.execute('search_exa', ARGS, '')
            restored = RetrievalTask(Path(directory), REQUEST)
            self.assertEqual(restored.bundle['exa_searches'][0]['http_status'],402)
            restored.bundle['exa_searches'][0]['status']='reserved'
            restored.save()
            with self.assertRaises(ValueError):
                RetrievalTask(Path(directory), REQUEST).execute('search_exa',ARGS,'')
            self.assertEqual(provider.call_count,1)

    def test_existing_ledger_does_not_gain_budget_when_key_is_added(self):
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'EXA_API_KEY':''}):
                task = RetrievalTask(Path(directory),REQUEST); task.save()
                task.bundle['acquisition_limits'].pop('exa_search'); task.save()
            with patch.dict(os.environ, {'EXA_API_KEY':'new-key'}):
                self.assertEqual(RetrievalTask(Path(directory),REQUEST).exa_limit,0)

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only-key'})
    @patch('ForecastAgent.runtime.retrieval.search_exa')
    def test_historical_leads_hide_titles_and_reject_unknown_or_boundary_dates(self, provider):
        provider.return_value={'results':[
            {'url':'https://example.org/old','published_date':'2026-02-01T00:00:00Z','provider':'exa','title':'Later outcome'},
            {'url':'https://example.org/unknown','published_date':None,'provider':'exa'},
            {'url':'https://example.org/new','published_date':'2026-03-01T00:00:00Z','provider':'exa'}],
            'raw_response':{},'request_payload':{}}
        with TemporaryDirectory() as directory:
            request={**REQUEST,'mode':'historical_exploratory','as_of_utc':'2026-03-01T00:00:00Z'}
            task=RetrievalTask(Path(directory),request); task.bundle['plan']=[{'id':'n','priority':'critical'}]
            result=task.execute('search_exa',{**ARGS,'search_role':'recent'},'')
            self.assertEqual(len(result['results']),1)
            self.assertNotIn('title',result['results'][0])
            self.assertEqual(len(task.bundle['quarantine']),2)
            self.assertEqual(provider.call_args.kwargs['cutoff'],datetime(2026,3,1,tzinfo=timezone.utc))
            self.assertNotIn('https://example.org/new',task.catalog())

    @patch('ForecastAgent.providers.exa_search.urlopen')
    def test_provider_sends_one_cheap_search_and_deduplicates(self, transport):
        payload={'results':[{'url':'https://example.org/source?utm_source=test'},
                            {'url':'https://example.org/source'}, {'url':'https://metaculus.com/questions/1'},
                            {'url':'https://elsewhere.org/source'}],'costDollars':{'total':0.007}}
        transport.return_value.__enter__.return_value=io.BytesIO(json.dumps(payload).encode())
        result=search('Agency official','fake-key',include_domains=['example.org'],
                      cutoff=datetime(2026,3,1,tzinfo=timezone.utc))
        sent=json.loads(transport.call_args.args[0].data)
        self.assertEqual(sent['type'],'auto')
        self.assertEqual(sent['numResults'],10)
        self.assertNotIn('contents',sent)
        self.assertNotIn('outputSchema',sent)
        self.assertEqual(len(result['results']),1)
        self.assertEqual(transport.call_count,1)

    @patch('ForecastAgent.providers.exa_search.urlopen')
    def test_http_failure_is_not_retried_and_response_redacts_key(self, transport):
        transport.side_effect=HTTPError('https://api.exa.ai/search',429,'Limited',{},io.BytesIO(b'fake-key'))
        with self.assertRaises(ExaError) as error:
            search('Agency official','fake-key')
        self.assertEqual(error.exception.http_status,429)
        self.assertNotIn('fake-key',error.exception.response_body)
        self.assertEqual(transport.call_count,1)

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only-key'})
    @patch('ForecastAgent.runtime.retrieval.search_exa')
    def test_verification_replay_reuses_ledger_without_more_provider_calls(self, provider):
        from ForecastAgent.verify_exa import verify
        provider.return_value={'results':[],'raw_response':{},'request_payload':{},'request_id':'empty'}
        with TemporaryDirectory() as directory:
            first=verify(directory)
            second=verify(directory)
            self.assertFalse(first['success'])
            self.assertEqual(second['search_attempts'],1)
            self.assertEqual(provider.call_count,1)

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only-key'})
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_ultra_has_optional_tool_and_redacts_exa_key_in_errors(self, model):
        def call(name,args,identifier):
            return {'tool_calls':[{'id':identifier,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        model.side_effect=[call('plan_evidence',{'needs':[{'id':'n','condition':'Release','priority':'critical','expected_source':'Agency','query':'Agency'}]},'plan'),
            RuntimeError('test-only-key failed')]
        with TemporaryDirectory() as directory:
            bundle=run_retrieval(REQUEST,directory,'','')
            self.assertIn('search_exa',[tool['function']['name'] for tool in model.call_args.kwargs['tools']])
            self.assertNotIn('test-only-key',json.dumps(bundle))
