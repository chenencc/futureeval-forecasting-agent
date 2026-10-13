"""Tavily3/4 routing, resume and physical accounting without real provider calls."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request
import urllib.request
from unittest.mock import patch

from ForecastAgent.market_repricing.common import policy, read, write
from ForecastAgent.market_repricing.worker import ProviderCap, provider_budget
from ForecastAgent.market_repricing.search_credentials import configured

ENV = {'TAVILY_API_KEY': 'fixture-key-three', 'TAVILY_API_KEY2': 'fixture-key-four'}


class Response(io.BytesIO):
    status = 200


def exhausted(code=432):
    return HTTPError('https://api.tavily.com/search', code, 'Quota exhausted', {}, io.BytesIO(b'{"detail":{"error":"Plan limit"}}'))


def request(path='/search'):
    return Request('https://api.tavily.com'+path, data=b'{"query":"fixture","search_depth":"basic"}',
        headers={'Authorization': 'Bearer '+ENV['TAVILY_API_KEY'], 'Content-Type': 'application/json'}, method='POST')


class SearchCredentials(unittest.TestCase):
    def test_primary_exhaustion_backup_and_restart_skip_failed_key(self):
        for path in ('/search','/extract'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as root, patch.dict(os.environ, ENV):
                calls = []
                def send(req, **kwargs):
                    calls.append((req.get_header('Authorization'), req.data, kwargs))
                    if len(calls)==1: raise exhausted()
                    return Response(b'{}')
                ledger = {'provider_reservations': []}
                with patch('urllib.request.urlopen', side_effect=send):
                    with provider_budget(root, ledger, 'task', policy()):
                        urllib.request.urlopen(request(path), timeout=30).close()
                    with provider_budget(root, ledger, 'task', policy()):
                        urllib.request.urlopen(request(path), timeout=30).close()
                self.assertEqual([r[0] for r in calls], ['Bearer fixture-key-three','Bearer fixture-key-four','Bearer fixture-key-four'])
                self.assertEqual(len(ledger['provider_reservations']),3)
                self.assertEqual([r['http_status'] for r in ledger['provider_reservations']],[432,200,200])
                self.assertTrue(all(r[1:]==calls[0][1:] for r in calls))
                for file in Path(root).rglob('*.json'):
                    self.assertNotIn(ENV['TAVILY_API_KEY'],file.read_text())
                    self.assertNotIn(ENV['TAVILY_API_KEY2'],file.read_text())

    def test_promoted_backup_exhaustion_can_return_to_recovered_primary(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, ENV):
            calls=[];ledger={'provider_reservations':[]}
            def send(req):
                auth=req.get_header('Authorization');calls.append(auth)
                if auth=='Bearer fixture-key-four':raise exhausted(433)
                return Response(b'{}')
            route=Path(root)/'provider-transport/tavily/route.json'
            with patch('urllib.request.urlopen',return_value=Response(b'{}')):
                with provider_budget(root,ledger,'task',policy()):urllib.request.urlopen(request()).close()
            # The real router writes identities; retain them when simulating a
            # prior default promotion. Both credentials have usable allowance.
            import hashlib
            write(route,{'schema_version':1,'provider':'tavily','credential_digests':[
                hashlib.sha256(ENV[k].encode()).hexdigest() for k in ('TAVILY_API_KEY','TAVILY_API_KEY2')],
                'default_role':'backup','exhausted':{}})
            with patch('urllib.request.urlopen',side_effect=send):
                with provider_budget(root,ledger,'task',policy()):urllib.request.urlopen(request()).close()
            self.assertEqual(calls,['Bearer fixture-key-four','Bearer fixture-key-three'])
            self.assertEqual(read(route)['exhausted']['backup']['http_status'],433)

    def test_both_exhausted_fail_fast_without_retransmission(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,ENV):
            ledger={'provider_reservations':[]}
            with patch('urllib.request.urlopen',side_effect=exhausted()) as send:
                for _ in range(2):
                    with provider_budget(root,ledger,'task',policy()),self.assertRaises(HTTPError):
                        urllib.request.urlopen(request())
            self.assertEqual(send.call_count,2)
            self.assertEqual(len(ledger['provider_reservations']),2)

    def test_physical_backup_cannot_bypass_dispatch_cap(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,ENV):
            ledger={'provider_reservations':[]};cfg=policy()|{'maximum_worker_search_attempts_per_dispatch':1}
            with patch('urllib.request.urlopen',side_effect=exhausted()) as send:
                with provider_budget(root,ledger,'task',cfg),self.assertRaises(ProviderCap):
                    urllib.request.urlopen(request())
            self.assertEqual(send.call_count,1)
            self.assertEqual(len(ledger['provider_reservations']),1)

    def test_service_and_auth_failures_do_not_switch(self):
        for code in (400,401,403,429,500,502):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as root,patch.dict(os.environ,ENV):
                ledger={'provider_reservations':[]}
                with patch('urllib.request.urlopen',side_effect=exhausted(code)) as send:
                    with provider_budget(root,ledger,'task',policy()),self.assertRaises(HTTPError):urllib.request.urlopen(request())
                self.assertEqual(send.call_count,1)

    def test_workflow_mapping_and_configuration_do_not_expose_values(self):
        root=Path(__file__).resolve().parents[3]
        workflow=(root/'.github/workflows/market_research_worker.yaml').read_text()
        self.assertIn('TAVILY_API_KEY: ${{ secrets.TAVILY_KEY3 }}',workflow)
        self.assertIn('TAVILY_API_KEY2: ${{ secrets.TAVILY_KEY4 }}',workflow)
        with patch.dict(os.environ,ENV):
            result=configured()
        self.assertTrue(result['primary_configured'] and result['backup_configured'])
        self.assertFalse(any(value in json.dumps(result) for value in ENV.values()))


if __name__=='__main__':unittest.main()
