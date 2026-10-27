import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from metaculus_agent.debug_sequence import DebugStore,HistoricalClient,run_case,CASES,KEY
from metaculus_agent.storage import dumps,sha
from metaculus_agent.runner import initial_state
from pipeline_v2.contracts import digest
from test_runner import MemoryStore,post,sealed

class Native:
    def __init__(self):self.calls=[]
    def account(self):return {'id':309450}
    def post(self,ident):
        self.calls.append(str(ident));qid=dict(CASES)[str(ident)];p=post(ident=int(qid));p['id']=int(ident)
        p['question'].update(status='closed',actual_close_time='2026-01-01T00:00:00Z',scheduled_close_time='2026-01-01T00:00:00Z',spot_scoring_time='2026-01-01T00:00:00Z',
            my_forecasts=dict(latest={'author_id':309450},history=[]))
        return p

def seed():
    backend=MemoryStore();budget=dumps(dict(days={'2026-10-27':{'model':17}},runs={},last_model_at=0));hashed=sha(budget)
    state=initial_state('production');state['runtime']={'budgets/control/provider-budget.jsonl':hashed}
    backend.publish({KEY:dumps(state),'metaculus/_control/objects/'+hashed:budget});return backend

def capture(task,p,root,checkpoint):
    return sealed(task,root/'fixture')

def analyze(value,root,checkpoint):
    root.mkdir(parents=True,exist_ok=True);(root/'receipt.json').write_text('{"fixture":true}');checkpoint()
    return dict(question_id=value['question_id'],run_id=value['run_id'],snapshot_id=value['snapshot_id'],context_sha256=digest(value),candidate=dict(payload={'question':int(value['question_id'].split(':')[1]),'probability_yes':0.5},comment='Synthetic only'))

class DebugTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,dict(os.environ));self.env.start();self.addCleanup(self.env.stop)
    def test_scopes_checkpoint_objects_products_and_rejects_traversal(self):
        b=MemoryStore();s=DebugStore(b,'a'*32)
        s.publish({KEY:b'control','metaculus/_control/objects/hash':b'payload','metaculus/start_date=2026-10-27/context.parquet':b'product'})
        self.assertTrue(all(k.startswith('metaculus/_debug/'+'a'*32+'/') for k in b.history[b.head]))
        with self.assertRaises(ValueError):s.publish({'metaculus/../../production':b'bad'})
        with self.assertRaises(ValueError):DebugStore(b,'../bad')
    def test_simulation_changes_only_eligibility_view_and_prohibits_submit(self):
        native=Native();client=HistoricalClient(native,*CASES[0]);view=client.post(CASES[0][0]);saved=copy.deepcopy(client.original)
        self.assertEqual(view['question']['status'],'open');self.assertEqual(view['question']['my_forecasts'],{'latest':None,'history':[]})
        self.assertEqual(saved['question']['status'],'closed');self.assertEqual(saved['question']['scheduled_close_time'],'2026-01-01T00:00:00Z')
        self.assertNotIn('my_forecasts',json.dumps(saved))
        with self.assertRaisesRegex(RuntimeError,'DEBUG_SUBMISSION_FORBIDDEN'):client.request('POST','questions/bulk-forecast-comment/',{})
    def test_three_arrivals_restore_retire_and_keep_production_bytes(self):
        b=seed();production=copy.deepcopy(b.history[b.head]);native=Native();session='b'*32
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(1,4):
                report=run_case(b,Path(tmp)/str(i),session,i,native,collector=capture,analyzer=analyze)
                self.assertEqual(report['status'],'debug_case_passed',report);self.assertEqual(report['completed_cases'],list(range(1,i+1)))
                self.assertTrue(report['local_payloads_retired']);self.assertTrue(report['budget_restored']);self.assertEqual(report['submissions'],0)
                self.assertTrue(report['simulated_submission_readback']);self.assertEqual(report['simulated_duplicate_posts'],0)
                self.assertEqual(report['budget_days']['2026-10-27']['model'],17)
        for key,value in production.items():self.assertEqual(b.history[b.head][key],value)
        added=set(b.history[b.head])-set(production)
        self.assertTrue(all(x.startswith('metaculus/_debug/'+session+'/') for x in added))
    def test_first_failure_blocks_second_and_preserves_saved_state(self):
        b=seed();native=Native();session='c'*32
        def fail(*args):raise RuntimeError('INJECTED_ANALYSIS_FAILURE')
        with tempfile.TemporaryDirectory() as tmp:
            r=run_case(b,Path(tmp)/'one',session,1,native,collector=capture,analyzer=fail)
            self.assertEqual(r['status'],'debug_stopped_on_failure');self.assertEqual(r['stage'],'analyzing')
            count=len(native.calls)
            with self.assertRaisesRegex(RuntimeError,'DEBUG_PREDECESSOR_NOT_PASSED'):run_case(b,Path(tmp)/'two',session,2,native,collector=capture,analyzer=analyze)
            self.assertEqual(len(native.calls),count)
            scope=DebugStore(b,session);state=json.loads(scope.read(KEY));self.assertTrue(state['runtime'])
            self.assertTrue(scope.exists('metaculus/case-1/audit.json'))
    def test_reusing_session_cannot_recollect_or_reset_budget(self):
        b=seed();session='d'*32
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_case(b,Path(tmp)/'one',session,1,Native(),collector=capture,analyzer=analyze)['status'],'debug_case_passed')
            with self.assertRaisesRegex(RuntimeError,'DEBUG_SESSION_ALREADY_STARTED'):run_case(b,Path(tmp)/'again',session,1,Native(),collector=capture,analyzer=analyze)
    def test_model_consumption_survives_each_fresh_case_root(self):
        b=seed();session='e'*32
        def consumed(value,root,checkpoint):
            path=Path(os.environ['FORECAST_V2_SHARED_BUDGET_ROOT'])/'control/provider-budget.jsonl'
            budget=json.loads(path.read_bytes());budget['days']['2026-10-27']['model']+=1;path.write_bytes(dumps(budget));checkpoint()
            return analyze(value,root,checkpoint)
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(1,4):
                r=run_case(b,Path(tmp)/str(i),session,i,Native(),collector=capture,analyzer=consumed)
                self.assertEqual(r['status'],'debug_case_passed',r);self.assertEqual(r['budget_days']['2026-10-27']['model'],17+i)
if __name__=='__main__':unittest.main()
