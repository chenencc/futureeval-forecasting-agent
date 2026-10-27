import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from metaculus_agent.storage import QuestionBatch,RuntimeCheckpoint,dumps,Conflict,sha
from metaculus_agent.runner import Runner,initial_state
from metaculus_agent.debug_sequence import batched_case,DebugStore,KEY
from pipeline_v2.contracts import digest
from test_runner import MemoryStore,Client,post,sealed
from test_debug_sequence import seed,Native,capture,analyze

class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,dict(os.environ));self.env.start();self.addCleanup(self.env.stop)
    def runner(self,*,live=True,collector=None,analyzer=None,client=None):
        self.backend=MemoryStore();state=initial_state('fixture');state['migration_complete']=True
        self.backend.publish({KEY:dumps(state)});self.start=self.backend.head
        self.batch=QuestionBatch(self.backend,self.root/'stage');cp=RuntimeCheckpoint(self.batch,self.root/'writer')
        def collect(task,p,root,checkpoint):
            self.assertEqual(self.backend.head,self.start)
            path=Path(os.environ['FORECAST_V2_SHARED_BUDGET_ROOT'])/'control/provider-budget.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(dumps({'days':{'2026-10-28':{'model':1}}}));checkpoint()
            self.assertEqual(self.backend.head,self.start)
            return sealed(task,root/'capture')
        def analysis(value,root,checkpoint):
            self.assertEqual(self.backend.head,self.start);checkpoint();self.assertEqual(self.backend.head,self.start)
            return dict(snapshot_id=value['snapshot_id'],context_sha256=digest(value),candidate=dict(payload={'question':100,'probability_yes':.6},comment='Fixture only'))
        return Runner(cp,client or Client(post()),writer='fixture',live=live,collector=collector or collect,analyzer=analyzer or analysis)
    def test_no_remote_write_until_submission_then_one_complete_atomic_batch(self):
        owner=self
        class ObservedClient(Client):
            def request(self,*args):
                owner.assertEqual(owner.backend.head,owner.start)
                owner.assertTrue(any((owner.root/'stage').iterdir()))
                return super().request(*args)
        client=ObservedClient(post());runner=self.runner(client=client)
        self.assertEqual(runner.run([post()])['failures'],0);self.assertEqual(client.posts,1)
        self.assertEqual(int(self.backend.head)-int(self.start),1);self.assertEqual(self.batch.successful_commits,1)
        state=json.loads(self.backend.read(KEY));task=state['tasks']['100']
        self.assertEqual(task['status'],'accepted');self.assertFalse(any(k.startswith('tasks/100/') for k in state['runtime']))
        self.assertFalse((runner.root/'tasks/100').exists())
        self.assertIn('budgets/control/provider-budget.jsonl',state['runtime'])
        archive=json.loads(self.backend.read(task['runtime_archive_key']));self.assertTrue(archive['runtime'])
        for hashed in archive['runtime'].values():self.assertEqual(sha(self.backend.read('metaculus/_control/objects/'+hashed)),hashed)
        for key in (task['context_key'],task['analysis_key'],task['runtime_archive_key']):self.assertTrue(self.backend.exists(key))
        cold=RuntimeCheckpoint(self.backend,self.root/'cold');saved=cold.restore()
        self.assertEqual(saved['tasks']['100']['analysis_revision'],self.backend.head)
        self.assertFalse((cold.root/'tasks/100').exists());self.assertTrue((cold.root/'budgets/control/provider-budget.jsonl').exists())
    def test_failed_cloud_flush_does_not_delete_native_files(self):
        runner=self.runner();self.backend.fail=True
        with self.assertRaises(Conflict):runner.run([post()])
        self.assertTrue((runner.root/'tasks/100').exists());self.assertTrue(self.batch.pending)
        self.assertEqual(self.backend.head,self.start);self.assertEqual(runner.client.posts,1)
        # Retry the same staged batch, not collection/analysis/submission.
        self.backend.fail=False;runner.flush_question();self.assertFalse((runner.root/'tasks/100').exists())
        self.assertEqual(runner.client.posts,1);self.assertEqual(self.batch.successful_commits,1)
    def test_capture_failure_saves_partial_runtime_once(self):
        def fail(task,p,root,checkpoint):
            path=root/'capture'/'receipt.json';path.parent.mkdir(parents=True);path.write_text('{"attempt":1}')
            checkpoint();self.assertEqual(self.backend.head,self.start);raise RuntimeError('CAPTURE_FIXTURE_FAILED')
        runner=self.runner(collector=fail)
        self.assertEqual(runner.run([post()])['failures'],1);self.assertEqual(self.batch.successful_commits,1)
        cp=RuntimeCheckpoint(self.backend,self.root/'recovery');state=cp.restore()
        self.assertEqual(state['tasks']['100']['last_error'],'CAPTURE_FIXTURE_FAILED')
        self.assertTrue((cp.root/'tasks/100/capture/receipt.json').exists());self.assertEqual(runner.client.posts,0)
    def test_empty_poll_has_no_commit(self):
        runner=self.runner();self.assertEqual(runner.run([])['processed'],0)
        self.assertEqual(self.backend.head,self.start);self.assertEqual(self.batch.successful_commits,0)
    def test_changed_remote_owner_blocks_before_provider_work(self):
        runner=self.runner(collector=lambda *args:self.fail('stale writer collected'))
        changed=json.loads(self.backend.read(KEY));changed['writer']='another-owner'
        self.backend.publish({KEY:dumps(changed)})
        with self.assertRaisesRegex(Conflict,'HF_WRITER_CHANGED'):runner.run([post()])
        self.assertEqual(runner.client.posts,0);self.assertEqual(self.batch.successful_commits,0)
    def test_staging_is_not_a_cloud_receipt_and_unchanged_versions_are_pruned(self):
        backend=MemoryStore();batch=QuestionBatch(backend,self.root/'stage')
        first=dumps({'used':1});second=dumps({'used':2})
        for raw in (first,second):
            hashed=sha(raw);receipt=batch.publish({'metaculus/_control/objects/'+hashed:raw,KEY:dumps({'schema':'metaculus-checkpoint/1','runtime':{'budgets/used.json':hashed}})})
            self.assertFalse(receipt['verified']);self.assertIsNone(receipt['revision']);self.assertEqual(backend.head,'0')
        batch.flush();self.assertEqual(backend.head,'1')
        self.assertFalse(backend.exists('metaculus/_control/objects/'+sha(first)))
        self.assertEqual(backend.read('metaculus/_control/objects/'+sha(second)),second)
    def test_three_debug_cases_each_have_one_backend_commit(self):
        backend=seed();production=backend.read(KEY);session='f'*32
        for i in range(1,4):
            before=int(backend.head)
            report=batched_case(backend,self.root/str(i),session,i,Native(),collector=capture,analyzer=analyze)
            self.assertEqual(report['status'],'debug_case_passed',report)
            self.assertEqual(int(backend.head)-before,1);self.assertEqual(report['hf_successful_commits'],1)
            self.assertTrue(report['exact_revision_readback']);self.assertTrue(report['local_payloads_retired'])
            audit=json.loads(DebugStore(backend,session).read(f'metaculus/case-{i}/audit.json'))
            self.assertEqual(audit['status'],'debug_case_prepared');self.assertFalse(audit['exact_revision_readback'])
        self.assertEqual(backend.read(KEY),production)
    def test_failed_debug_case_has_one_failure_batch_and_blocks_next(self):
        backend=seed();session='1'*32;before=int(backend.head)
        def fail(*args):raise RuntimeError('FIXTURE_ANALYSIS_FAILED')
        report=batched_case(backend,self.root/'one',session,1,Native(),collector=capture,analyzer=fail)
        self.assertEqual(report['status'],'debug_stopped_on_failure');self.assertEqual(int(backend.head)-before,1)
        with self.assertRaisesRegex(RuntimeError,'DEBUG_PREDECESSOR_NOT_PASSED'):
            batched_case(backend,self.root/'two',session,2,Native(),collector=capture,analyzer=analyze)

if __name__=='__main__':unittest.main()
