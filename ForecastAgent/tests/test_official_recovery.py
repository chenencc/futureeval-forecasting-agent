"""Fault regressions for immutable URL lookup, bounded recovery and deadlines."""
import copy
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from unittest import TestCase
from ForecastAgent.readers.saved import select,SavedSourceLookupError,SavedSourceIdentityError
from ForecastAgent.competition.recovery_policy import recover_legacy_lookup,classify,health,LEGACY_LOOKUP_ERROR

class RecoveryTests(TestCase):
    def test_exact_slash_identity_and_alias_preserve_input(self):
        pages={'https://example.org/topic/':{'content':'original'}};before=copy.deepcopy(pages)
        self.assertEqual(select(pages,'https://example.org/topic/')[1],'original')
        self.assertEqual(select(pages,'https://example.org/topic')[1],'original')
        self.assertEqual(pages,before)
    def test_conflicting_alias_is_not_silently_merged(self):
        pages={'https://example.org/topic/':{'content':'old'},'https://example.org/topic':{'content':'new'}}
        self.assertEqual(select(pages,'https://example.org/topic/')[1],'old')
        with self.assertRaises(SavedSourceIdentityError):select(pages,'https://example.org/topic/?utm_source=a')
    def test_unknown_lookup_is_recoverable_but_identity_is_frozen(self):
        now=datetime(2026,10,10,8,tzinfo=timezone.utc)
        with self.assertRaises(SavedSourceLookupError):select({},'https://example.org')
        task={'id':'1','deadline_utc':'2026-10-10T08:20:00Z'}
        self.assertEqual(classify(SavedSourceLookupError('missing'),task,now),('recovery_wait',now.isoformat()))
        self.assertEqual(classify(SavedSourceIdentityError('collision'),task,now),('blocked_integrity',None))
    def test_only_diagnosed_pre_submission_block_is_reopened_once(self):
        task={'stage':'blocked_integrity','last_error':LEGACY_LOOKUP_ERROR,'collection_executions':1}
        self.assertFalse(recover_legacy_lookup(task,has_input=True,has_submission=True))
        self.assertTrue(recover_legacy_lookup(task,has_input=True,has_submission=False))
        self.assertEqual(task['collection_executions'],1)
        self.assertFalse(recover_legacy_lookup(task,has_input=True,has_submission=False))
    def test_unknown_failure_is_bounded_and_attention_is_visible(self):
        now=datetime(2026,10,10,8,tzinfo=timezone.utc);task={'id':'1','deadline_utc':'2026-10-10T08:20:00Z'}
        for _ in range(3):stage,retry=classify(RuntimeError('service unavailable'),task,now)
        self.assertEqual((stage,retry),('needs_review',None));task['stage']=stage
        self.assertEqual(health({'1':task},now)['deadline_at_risk_ids'],['1'])
        self.assertFalse(health({'1':task},now)['healthy'])
