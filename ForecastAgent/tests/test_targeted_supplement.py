"""Durable sidecar allowances must not reset, repeat or mutate parent files."""
import tempfile
from pathlib import Path
from unittest import TestCase
from ForecastAgent.supplement.targeted import Ledger,parent_counts


class TargetedBudgetTests(TestCase):
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
