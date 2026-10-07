"""Durable sidecar allowances must not reset, repeat or mutate parent files."""
import tempfile
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from ForecastAgent.supplement import targeted
from ForecastAgent.supplement.targeted import Ledger,parent_counts,eligible
from ForecastAgent.supplement.stage import save


class TargetedBudgetTests(TestCase):
    def test_new_sidecar_keeps_original_and_replay_uses_no_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=root/'original'
            save(parent/'bundle.json',{'request':{'id':'1','question':'Official growth report',
                'resolution_criteria':'Missing official rules'},'result':{'status':'empty'}})
            original=(parent/'bundle.json').read_bytes()
            config={'id':'1','parent_collection':str(parent),'query':'Official growth report'}
            search={'results':[{'url':'https://example.org/report','title':'Official growth report'}]}
            body='This official publication describes economic observations, reporting periods and data sources. '*25
            with patch.dict(os.environ,{'TAVILY_API_KEY':'offline','EXA_API_KEY':'offline'}), \
                 patch.object(targeted,'search_batch',return_value=search) as tavily, \
                 patch.object(targeted,'exa_search',return_value=search) as exa, \
                 patch.object(targeted,'fetch_document',return_value={'content':body,'retrieved_at_utc':'2026-10-07T01:00:00Z',
                     'body_diagnostics':{'usable_text':True}}) as fetch:
                first=targeted.run_one(config,root/'new-deep-directory/tasks')
                second=targeted.run_one(config,root/'new-deep-directory/tasks')
            self.assertEqual(first,second);self.assertEqual(first['usable_bodies'],1)
            self.assertEqual(tavily.call_count,1);self.assertEqual(exa.call_count,1)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual((parent/'bundle.json').read_bytes(),original)

    def test_private_literal_and_platform_urls_are_excluded(self):
        for url in ('http://127.0.0.1/','http://10.0.0.1/','http://localhost/','https://x.local/a',
                    'https://www.metaculus.com/questions/1/'):
            self.assertFalse(eligible(url))
        self.assertTrue(eligible('https://example.org/report'))

    def test_reserved_unknown_search_is_consumed_and_not_repeated_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'ledger.json';identity={'parent_counts':{'tavily_basic':2}}
            first=Ledger(p,identity);first.reserve('tavily_basic','query',{'query':'query'})
            second=Ledger(p,identity)
            self.assertEqual(second.remaining('tavily_basic'),0)
            self.assertIsNone(second.reserve('tavily_basic','other',{}))
            self.assertIsNone(second.reserve('tavily_basic','query',{}))

    def test_prior_extract_probe_and_old_failed_http_consume_original_allowances(self):
        bundle={'searches':[{},{}],'exa_searches':[{}],'fetch_attempts':[{}]*8}
        supplement={'attempts':[{'method':'http','status':'failed'}]*2}
        count=parent_counts(bundle,supplement,prior_extract=1)
        self.assertEqual(count['extract'],1);self.assertEqual(count['supplement_http'],2)
        self.assertEqual(count['initial_http'],8);self.assertEqual(count['exa'],1)

    def test_parent_or_grant_change_cannot_reopen_finished_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'ledger.json';identity={'parent_counts':{'extract':0}}
            first=Ledger(p,identity);first.data['finished']=True;first.flush()
            self.assertIsNone(Ledger(p,identity).reserve('extract','urls',{}))
            with self.assertRaises(ValueError):Ledger(p,{'parent_counts':{'extract':1}})
