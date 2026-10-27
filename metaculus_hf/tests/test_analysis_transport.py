import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request
from urllib.error import HTTPError
from metaculus_agent.analysis_transport import installed
from ForecastAgent.providers import decisions

class AnalysisTransportTests(unittest.TestCase):
    def test_explicit_credit_exhaustion_uses_backup_with_durable_attempts(self):
        events=[];calls=[];wall=[100.]
        def clock():wall[0]+=5;return wall[0]
        def checkpoint():events.append('checkpoint')
        def opener(req,*a,**kw):
            self.assertEqual(events[-1],'checkpoint');calls.append(req.get_header('Authorization'));events.append('http')
            if len(calls)==1:raise HTTPError(req.full_url,402,'exhausted',{},io.BytesIO(b'{"error":{"message":"Insufficient credits","metadata":{}}}'))
            return type('Response',(),{'status':200})()
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture-primary','OPENROUTER_API_KEY2':'fixture-backup','FORECAST_V2_SHARED_BUDGET_ROOT':''}),patch('pipeline_v2.providers.time.time',side_effect=clock):
            with installed(tmp,'test-analysis',checkpoint,opener=opener):
                response=decisions.urlopen(Request('https://openrouter.ai/api/alpha/decisions',data=b'{}',headers={'Authorization':'Bearer fixture-primary'}))
            self.assertEqual(response.status,200);self.assertEqual(calls,['Bearer fixture-primary','Bearer fixture-backup'])
            ledger=json.loads((Path(tmp)/'control/provider-budget.jsonl').read_text());self.assertEqual(ledger['runs']['test-analysis']['model'],2)
            saved=''.join(p.read_text() for p in (Path(tmp)/'analysis-provider-transport').rglob('*.json'))
            self.assertNotIn('fixture-primary',saved);self.assertNotIn('fixture-backup',saved)
    def test_generic_502_does_not_switch_credential_or_erase_budget(self):
        calls=[]
        def opener(req,*a,**kw):calls.append(req);raise HTTPError(req.full_url,502,'temporary',{},io.BytesIO(b'{"error":{"message":"server error"}}'))
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture-primary','OPENROUTER_API_KEY2':'fixture-backup','FORECAST_V2_SHARED_BUDGET_ROOT':''}):
            with installed(tmp,'test-analysis',lambda:None,opener=opener):
                with self.assertRaises(HTTPError):decisions.urlopen(Request('https://openrouter.ai/api/alpha/decisions',data=b'{}',headers={'Authorization':'Bearer fixture-primary'}))
            self.assertEqual(len(calls),1)
    def test_failed_checkpoint_prevents_http(self):
        def stop():raise RuntimeError('injected persistence failure')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture-primary','OPENROUTER_API_KEY2':'fixture-backup','FORECAST_V2_SHARED_BUDGET_ROOT':''}):
            with installed(tmp,'test-analysis',stop,opener=lambda *_:self.fail('HTTP after failed checkpoint')):
                with self.assertRaises(RuntimeError):decisions.urlopen(Request('https://openrouter.ai/api/alpha/decisions',data=b'{}',headers={'Authorization':'Bearer fixture-primary'}))

if __name__=='__main__':unittest.main()
