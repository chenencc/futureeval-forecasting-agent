"""Offline scheduler tests; no workflow dispatch or acquisition provider calls."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest import TestCase
from ForecastAgent.continuation import next_batch


def campaign():
    return {'forecast_submissions':False,'continuation':{'enabled':True,
        'authorization_reason':'User authorized finishing the frozen queue in batches of five.'},
        'limits':{'model_http_campaign':1600,'executions_per_task':3},
        'tasks':{str(i):{'status':'pending','attempts':[],'resources':{}} for i in range(12)}}


class ContinuationTests(TestCase):
    def test_only_five_untouched_tasks_and_no_ledger_mutation(self):
        c=campaign();before=deepcopy(c);p=next_batch(c)
        self.assertEqual(p['question_ids'],['0','1','2','3','4'])
        self.assertEqual(p['inputs']['limit'],'5');self.assertEqual(c,before)

    def test_provider_pause_is_preserved_as_wait(self):
        c=campaign();c['pause_until_utc']='2026-10-02T02:00:00Z'
        p=next_batch(c,datetime(2026,10,2,1,tzinfo=timezone.utc))
        self.assertEqual(p['wait_until_utc'],'2026-10-02T02:00:00+00:00')
        c['requires_provider_review']=True
        self.assertEqual(next_batch(c)['reason'],'provider_review_required')

    def test_retry_has_existing_task_reason_and_execution_cap(self):
        c=campaign()
        for t in c['tasks'].values():t['status']='closed_with_gaps'
        t=c['tasks']['0'];t.update(status='incomplete',attempts=[{}],failure={'retryable':True,'category':'provider_unavailable'})
        p=next_batch(c);self.assertEqual(p['inputs']['question'],'0')
        self.assertIn('execution 2',p['inputs']['resume_reason'])
        t['attempts']=[{},{},{}]
        self.assertEqual(next_batch(c)['reason'],'requires_attention')

    def test_finished_queue_never_reopens_closed_tasks(self):
        c=campaign()
        for t in c['tasks'].values():t['status']='closed_with_gaps'
        self.assertEqual(next_batch(c)['reason'],'queue_finished')

    def test_campaign_budget_and_operator_review_stop_dispatch(self):
        c=campaign();c['tasks']['0']['resources']['model_http']=1600
        self.assertEqual(next_batch(c)['reason'],'campaign_model_budget_exhausted')
        c['continuation']['enabled']=False
        self.assertEqual(next_batch(c)['reason'],'not_authorized')
