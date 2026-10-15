import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.engineering.common import connect, digest
from ForecastAgent.engineering.lifecycle import record, latest, recovery, failure_code
from ForecastAgent.engineering.trace import Trace, summary
from .fixtures import CLOCK


def event(state, at=CLOCK, **extra):
    return {'schema':'forecast-lifecycle-event-v1','domain':'experiment','task_key':'x','stage':'analysis',
            'state':state,'occurred_at':at,'retry_at':None,'failure_code':None,'source_sha256':digest(state),'native':False, **extra}


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.db = connect(self.folder.name); self.addCleanup(self.db.close)

    def test_stage_must_start_queued_and_terminal_never_reopens(self):
        with self.assertRaises(ValueError): record(self.db, event('running'))
        for state in ('queued','running','completed'): record(self.db, event(state))
        with self.assertRaises(ValueError): record(self.db, event('running', '2026-10-14T12:01:00Z'))

    def test_duplicate_event_is_idempotent(self):
        self.assertTrue(record(self.db, event('queued')))
        self.assertFalse(record(self.db, event('queued')))

    def test_retry_wait_clock_and_deadline(self):
        record(self.db,event('queued')); record(self.db,event('running'))
        wait = event('retry_wait', retry_at='2026-10-14T13:00:00Z', failure_code='provider_service')
        record(self.db, wait)
        self.assertEqual(recovery(wait,CLOCK)['action'],'wait_deadline')
        with self.assertRaises(ValueError): record(self.db,event('running','2026-10-14T12:30:00Z'))
        record(self.db,event('running','2026-10-14T13:00:00Z'))

    def test_unknown_submission_requires_readback_and_acceptance_is_delivery_only(self):
        unknown = event('submission_unknown',stage='delivery',native=True,failure_code='unknown_http_outcome')
        record(self.db,unknown)
        self.assertEqual(recovery(unknown,CLOCK)['action'],'readback_only')
        with self.assertRaises(ValueError): record(self.db,event('running',stage='delivery'))
        record(self.db,event('accepted',stage='delivery',native=True))
        with self.assertRaises(ValueError): record(self.db,event('accepted',task_key='other',native=True))

    def test_native_imports_are_observations_and_do_not_grant_execution(self):
        e = event('retry_wait',native=True)
        record(self.db,e)
        plan = recovery(e,CLOCK)
        self.assertEqual(plan['action'],'requires_repair')
        self.assertFalse(plan['automatic_execution'])
        self.assertFalse(plan['budget_reset'])

    def test_502_is_service_failure_not_account_quota(self):
        class Http(Exception): code = 502
        self.assertEqual(failure_code(Http()),'provider_service')
        self.assertEqual(failure_code(FileNotFoundError()),'source_unavailable')

    def test_native_overdue_retry_is_observed_without_rewriting_its_deadline(self):
        e=event('retry_wait','2026-10-14T13:00:00Z',native=True,retry_at=CLOCK)
        record(self.db,e)
        self.assertEqual(recovery(e,'2026-10-14T13:00:00Z')['action'],'native_resume_from_checkpoint')

    def test_budget_reset_deadline_is_not_a_budget_reset_operation(self):
        e=event('retry_wait',native=True,retry_at='2026-10-15T00:00:00Z',failure_code='budget_exhausted')
        self.assertEqual(recovery(e,CLOCK)['action'],'wait_deadline')
        p=recovery(e,'2026-10-15T00:00:00Z')
        self.assertEqual(p['action'],'native_resume_from_checkpoint');self.assertFalse(p['budget_reset'])


class Tracing(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.db = connect(self.folder.name); self.addCleanup(self.db.close)

    def test_nested_spans_share_trace_and_have_real_durations(self):
        trace = Trace(self.db,'market','polymarket:123','dispatch')
        with trace.span('task') as parent:
            with trace.span('acquisition') as child: pass
        rows = list(self.db.execute('SELECT * FROM spans ORDER BY rowid'))
        self.assertEqual(rows[1]['parent_id'],parent['id'])
        self.assertEqual(rows[0]['trace_id'],rows[1]['trace_id'])
        self.assertTrue(all(r['duration_ms']>=0 for r in rows))
        s = summary(self.db)
        self.assertEqual(s['stages']['acquisition']['unknown_usage'],1)

    def test_exception_and_invalid_usage_preserve_failed_span(self):
        trace = Trace(self.db,'market','x','dispatch')
        with self.assertRaises(FileNotFoundError),trace.span('read'): raise FileNotFoundError('fixture')
        with self.assertRaises(ValueError),trace.span('model') as span: span['usage']={'cost_usd':float('nan')}
        self.assertEqual(self.db.execute('SELECT count(*) FROM spans WHERE status=\'failed\'').fetchone()[0],2)

    def test_secrets_are_not_accepted_as_trace_metadata(self):
        for value in ({'authorization':'hidden'},{'note':'Bearer not-a-real-credential'}):
            with self.assertRaises(ValueError): Trace(self.db,'market','x','dispatch',value)

    def test_native_function_can_return_a_failed_business_outcome(self):
        trace=Trace(self.db,'market','x','dispatch')
        with trace.span('task') as span:span['outcome']='failed';span['failure_code']='budget_exhausted'
        row=self.db.execute('SELECT status,error_code FROM spans').fetchone()
        self.assertEqual(tuple(row),('failed','budget_exhausted'))

    def test_process_kill_leaves_visible_incomplete_span(self):
        folder = str(Path(self.folder.name)/'killed')
        script = 'from ForecastAgent.engineering.common import connect; from ForecastAgent.engineering.trace import Trace; import os; d=connect('+repr(folder)+'); t=Trace(d,"market","x","kill"); s=t.span("analysis"); s.__enter__(); os._exit(17)'
        result = subprocess.run([sys.executable,'-c',script],capture_output=True)
        self.assertEqual(result.returncode,17,result.stderr)
        db = connect(folder)
        try: self.assertEqual(summary(db)['stages']['analysis']['incomplete'],1)
        finally: db.close()

    def test_native_worker_wrapper_preserves_results_and_restores_functions(self):
        from ForecastAgent.engineering.native import market_worker
        from ForecastAgent.market_repricing import worker
        def execute(root,ledger,key,cfg,http,collector,forecaster=None):
            return collector(ledger['tasks'][key],root)
        ledger={'tasks':{'x':{'status':'queued','executions':0}}}
        with patch.object(worker,'execute',execute):
            with market_worker(self.db,'dispatch'):
                self.assertEqual(worker.execute('root',ledger,'x',{},None,lambda task,root: {'same':True}),{'same':True})
            self.assertIs(worker.execute,execute)
        self.assertEqual(ledger,{'tasks':{'x':{'status':'queued','executions':0}}})
        self.assertEqual(summary(self.db)['stages']['acquisition_and_mapping']['completed'],1)
