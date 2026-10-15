from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ForecastAgent.engineering.common import connect, digest, get
from ForecastAgent.engineering import experiments as e
from .fixtures import CLOCK, packet, routes


def runner(value=.8):
    def call(p, route):
        return {'schema':'forecast-experiment-output-v1','case_id':p['case_id'],'input_sha256':digest(p),
                'kind':p['question']['kind'],'value':value}, None
    return call


class FrozenExperiments(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.db=connect(self.folder.name); self.addCleanup(self.db.close)
        self.p=packet()
        self.ident=e.freeze(self.db,'frozen-fixture',[self.p],routes())

    def test_manifest_and_cohort_are_immutable_and_named(self):
        self.assertEqual(e.freeze(self.db,'frozen-fixture',[self.p],routes()),self.ident)
        for mutation in ('prompt','body','model'):
            r=routes(); p=copy.deepcopy(self.p)
            if mutation=='prompt':r[0]['prompt']='changed'
            elif mutation=='model':r[0]['model']='another'
            else:p['gaps']=[{'code':'extra'}]
            with self.assertRaises(ValueError): e.freeze(self.db,'frozen-fixture',[p],r)

    def test_old_native_prompt_is_not_silently_used_for_replay(self):
        m=e.manifest(self.db,self.ident)
        self.assertFalse(m['labels_in_model_input'])
        self.assertFalse(m['native_tasks_modified'])
        self.assertEqual(get(self.db,m['cases'][0]['input_sha256']),self.p)

    def test_duplicate_case_and_secret_configs_rejected(self):
        with self.assertRaises(ValueError):e.freeze(self.db,'duplicate',[self.p,self.p],routes())
        r=routes();r[0]['config']['api_key']='never-store-this'
        with self.assertRaises(ValueError):e.freeze(self.db,'secret',[self.p],r)

    def test_real_runner_attempt_is_reserved_and_not_repeated(self):
        calls=[]
        def call(p,route):
            self.assertEqual(self.db.execute("SELECT status FROM runs WHERE mode='prediction'").fetchone()[0],'running')
            calls.append(p)
            return runner()(p,route)
        first=e.run(self.db,self.ident,self.p['case_id'],'a',call)
        again=e.run(self.db,self.ident,self.p['case_id'],'a',call)
        self.assertEqual(first['status'],'completed')
        self.assertTrue(again['reused']);self.assertEqual(len(calls),1)

    def test_offline_audit_does_not_consume_future_prediction_slot(self):
        self.assertEqual(e.run(self.db,self.ident,self.p['case_id'],'a',audit_only=True)['status'],'validated')
        self.assertEqual(e.run(self.db,self.ident,self.p['case_id'],'a',runner())['status'],'completed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM runs').fetchone()[0],2)

    def test_failed_output_is_saved_and_other_cases_routes_continue(self):
        bad=e.run(self.db,self.ident,self.p['case_id'],'a',runner({'invalid':'format'}))
        good=e.run(self.db,self.ident,self.p['case_id'],'b',runner(.6))
        self.assertEqual(bad['status'],'failed');self.assertEqual(good['status'],'completed')
        row=self.db.execute("SELECT output_sha256 FROM runs WHERE route_id='a'").fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(get(self.db,row[0])['value'],{'invalid':'format'})

    def test_output_must_bind_the_same_input(self):
        def wrong(p,route):
            result,usage=runner()(p,route);result['input_sha256']='0'*64;return result,usage
        self.assertEqual(e.run(self.db,self.ident,self.p['case_id'],'a',wrong)['status'],'failed')

    def test_implementation_change_requires_new_experiment(self):
        with patch.object(e,'implementation',return_value='a'*64),self.assertRaises(ValueError):
            e.run(self.db,self.ident,self.p['case_id'],'a',runner())

    def test_changed_execution_environment_is_not_silently_reused(self):
        with patch.object(e,'environment',return_value={'python':'different'}),self.assertRaises(ValueError):
            e.run(self.db,self.ident,self.p['case_id'],'a',runner())
        # Input hash audit remains portable and never executes that runner.
        with patch.object(e,'environment',return_value={'python':'different'}):
            self.assertEqual(e.run(self.db,self.ident,self.p['case_id'],'a',audit_only=True)['status'],'validated')

    def test_damaged_frozen_input_fails_before_runner(self):
        with self.db:self.db.execute("UPDATE objects SET bytes=X'0000' WHERE sha256=?",(digest(self.p),))
        with self.assertRaises(ValueError):e.run(self.db,self.ident,self.p['case_id'],'a',runner())
        self.assertEqual(self.db.execute('SELECT count(*) FROM runs').fetchone()[0],0)

    def test_no_readable_originals_never_call_runner(self):
        p=copy.deepcopy(self.p);p['sources']=[];p['gaps']=[{'code':'no_readable_originals'}]
        ident=e.freeze(self.db,'empty-original',[p],routes())
        call=__import__('unittest.mock',fromlist=['Mock']).Mock()
        result=e.run(self.db,ident,p['case_id'],'a',call)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['new_runner_calls'],0)
        call.assert_not_called()

    def test_binary_brier_and_pairing_with_labels_kept_out_of_input(self):
        e.run(self.db,self.ident,self.p['case_id'],'a',runner(.8))
        e.run(self.db,self.ident,self.p['case_id'],'b',runner(.6))
        e.add_label(self.db,self.ident,self.p['case_id'],'resolved_binary',1,'2026-10-15T00:00:00Z',source_sha256='a'*64)
        metrics=e.evaluate(self.db,self.ident)
        self.assertAlmostEqual(metrics['routes']['a']['brier'],.04)
        self.assertAlmostEqual(metrics['routes']['b']['brier'],.16)
        self.assertEqual(metrics['paired_cases'],1)
        self.assertNotIn('resolution',get(self.db,digest(self.p)))
        self.assertFalse(metrics['forecast_skill_claimed'])

    def test_unresolved_does_not_get_an_accuracy_or_invented_score(self):
        e.run(self.db,self.ident,self.p['case_id'],'a',runner())
        metric=e.evaluate(self.db,self.ident)
        self.assertIsNone(metric['routes']['a']['brier'])
        self.assertEqual(metric['routes']['b']['not_started'],1)

    def test_price_label_never_becomes_resolution_brier(self):
        e.run(self.db,self.ident,self.p['case_id'],'a',runner())
        e.add_label(self.db,self.ident,self.p['case_id'],'market_price',
                    {'value':.6,'target_at':'2026-10-15T12:00:00Z','origin_at':CLOCK,'quote_at':'2026-10-15T12:00:00Z','source_sha256':'b'*64},
                    '2026-10-15T13:00:00Z',source_sha256='b'*64)
        self.assertIsNone(e.evaluate(self.db,self.ident)['routes']['a']['brier'])

    def test_ambiguous_labels_require_adjudication(self):
        e.run(self.db,self.ident,self.p['case_id'],'a',runner())
        for value in (0,1):e.add_label(self.db,self.ident,self.p['case_id'],'resolved_binary',value,'2026-10-15T00:00:00Z',source_sha256=str(value)*64)
        self.assertIsNone(e.evaluate(self.db,self.ident)['routes']['a']['brier'])

    def test_parallel_claims_only_one_runner_call(self):
        counter=[]
        def job():
            db=connect(self.folder.name)
            try:
                def slow(p,r):counter.append(1);time.sleep(.1);return runner()(p,r)
                return e.run(db,self.ident,self.p['case_id'],'a',slow)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:job(),range(2)))
        self.assertEqual(len(counter),1)
        self.assertEqual(sum(r.get('new_runner_calls',0) for r in results),1)

    def test_interrupted_attempt_stays_visible_and_never_reexecutes(self):
        with self.db:
            ident=digest([self.ident,self.p['case_id'],'a','prediction'])
            self.db.execute('INSERT INTO runs(id,experiment_id,case_id,route_id,mode,status,started_at) VALUES(?,?,?,?,?,?,?)',
                            (ident,self.ident,self.p['case_id'],'a','prediction','running',CLOCK))
        result=e.run(self.db,self.ident,self.p['case_id'],'a',runner())
        self.assertTrue(result['reused']);self.assertEqual(result['status'],'running')
        self.assertEqual(e.evaluate(self.db,self.ident)['routes']['a']['incomplete'],1)

    def test_cli_doctor_and_status_do_not_require_network(self):
        for command in ('doctor','status'):
            result=subprocess.run([sys.executable,'-m','ForecastAgent.engineering','--root',self.folder.name,command],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            value=json.loads(result.stdout)
            self.assertTrue(value['schema'].startswith('forecast-'))
