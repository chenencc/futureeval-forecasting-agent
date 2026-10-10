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

    def test_review_stage_is_terminal_and_deadline_bounds_process(self):
        from ForecastAgent.releases import v1_0_5 as release
        from ForecastAgent.competition.recovery_policy import task_timeout
        now=datetime(2026,10,10,8,tzinfo=timezone.utc)
        self.assertIn('needs_review',release.live.TERMINAL)
        self.assertEqual(task_timeout({'deadline_utc':'2026-10-10T08:02:00Z'},1500,now),100)
    def test_optional_reread_keeps_first_without_provider_attempt(self):
        from types import SimpleNamespace
        from ForecastAgent.competition.decision_deadline import guard
        calls=[];original=lambda *a:calls.append(a) or 'validated'
        chain=SimpleNamespace(call=original)
        with tempfile.TemporaryDirectory() as tmp:
            with guard(chain,{'spot_scoring_time':datetime.now(timezone.utc).isoformat()}):
                self.assertEqual(chain.call({},Path(tmp)/'first',{}),'validated')
                with self.assertRaises(RuntimeError):chain.call({},Path(tmp)/'second',{})
            self.assertIs(chain.call,original)
            self.assertEqual(len(calls),1)
            self.assertTrue((Path(tmp)/'deadline-second-read-skipped.json').exists())

    def test_worker_bounds_local_failure_without_recollection_or_duplicate_delivery(self):
        from unittest.mock import patch
        import os
        from ForecastAgent.tests.test_release_1_0_5 import ProductionReleaseTests
        from ForecastAgent.releases import v1_0_5 as release,stress
        from ForecastAgent.competition.queue import load,save
        fixture=ProductionReleaseTests();fixture.setUp()
        try:
            incoming,client=fixture.args(fixture.docs(2));calls=[]
            def infer(source,folder,ident):
                calls.append(ident)
                if ident=='1':raise SavedSourceLookupError('missing local body')
                return release.analyze(source,folder,ident)
            with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',stress.decide):
                for attempt in range(4):
                    release.once(fixture.root/'worker',incoming,enabled=True,client=client,
                        collector=stress.saved_collector,infer=infer,limit=5)
                    state=load(fixture.root/'worker/campaign.json')
                    if state['tasks']['1']['stage']!='needs_review':
                        state['tasks']['1']['retry_at_utc']=None
                        save(fixture.root/'worker/campaign.json',state)
            self.assertEqual(calls.count('1'),3)
            self.assertEqual(calls.count('2'),1)
            self.assertEqual(state['tasks']['1']['stage'],'needs_review')
            self.assertEqual(state['tasks']['1']['collection_executions'],1)
            self.assertEqual(state['tasks']['2']['stage'],'accepted')
            self.assertEqual(len(client.writes),1)
        finally:fixture.doCleanups();fixture.tearDown()
