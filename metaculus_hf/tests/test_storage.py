import json,unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import requests
from huggingface_hub.errors import HfHubHTTPError
from metaculus_agent.storage import Store,Conflict,PersistenceError
from metaculus_agent.acceptance import storage,resume
from metaculus_agent.products import context,product,manifest
from metaculus_agent.storage import RuntimeCheckpoint,dumps,sha
from test_runner import MemoryStore,post,sealed
from metaculus_agent.runner import initial_state
from metaculus_agent.platform_adapter import live_request,rule_identity
from pipeline_v2.contracts import now

class Api:
    def __init__(self,private=True):self.private=private;self.head='a';self.files={'a':{'metaculus/_control/checkpoint.json':b'owner1'}};self.conflict=False;self.calls=0
    def repo_info(self,*a,**kw):return SimpleNamespace(private=self.private,sha=self.head)
    def file_exists(self,repo,key,**kw):return key in self.files[kw.get('revision',self.head)]
    def create_commit(self,*a,**kw):
        self.calls+=1
        if kw['parent_commit']!=self.head:
            response=requests.Response();response.status_code=409
            raise HfHubHTTPError('injected CAS conflict',response=response)
        new=self.head+'n';self.files[new]=dict(self.files[self.head]);self.head=new
        for op in kw['operations']:self.files[new][op.path_in_repo]=op.path_or_fileobj
        return SimpleNamespace(oid=new)
    def advance(self,updates):
        old=self.head;self.head+='x';self.files[self.head]={**self.files[old],**updates}

class StoreTests(unittest.TestCase):
    def store(self,api):
        s=Store('private/dataset','unused-fixture',api=api);s.read=lambda key,revision=None:api.files[revision or s.head][key]
        s.guards['metaculus/_control/checkpoint.json']=b'owner1';return s
    def test_public_destination_refused(self):
        with self.assertRaisesRegex(ValueError,'PRIVATE_DATASET_REQUIRED'):self.store(Api(False))
    def test_other_dataset_writer_can_advance_without_losing_files(self):
        api=Api();s=self.store(api);api.advance({'studies/foreign':b'preserved'})
        r=s.publish({'metaculus/_control/checkpoint.json':b'owner1-stage2'})
        self.assertTrue(r['verified']);self.assertEqual(api.files[api.head]['studies/foreign'],b'preserved')
        self.assertEqual(api.calls,2);self.assertEqual(s.guards['metaculus/_control/checkpoint.json'],b'owner1-stage2')
    def test_changed_ownership_checkpoint_fences_old_runner(self):
        api=Api();s=self.store(api);api.advance({'metaculus/_control/checkpoint.json':b'owner2'})
        with self.assertRaisesRegex(Conflict,'HF_WRITER_CHANGED'):s.publish({'metaculus/_control/checkpoint.json':b'old-writer-overwrite'})
        self.assertEqual(api.files[api.head]['metaculus/_control/checkpoint.json'],b'owner2')
    def test_first_checkpoint_creation_rebases_only_when_still_absent(self):
        api=Api();s=self.store(api);key='metaculus/_control/new-owner.json';s.guards={key:None}
        api.advance({'studies/foreign':b'other writer'})
        receipt=s.publish({key:b'new owner'});self.assertTrue(receipt['verified'])
        self.assertEqual(api.files[api.head]['studies/foreign'],b'other writer')
        api=Api();s=self.store(api);s.guards={key:None};api.advance({key:b'competing owner'})
        with self.assertRaises(Conflict):s.publish({key:b'overwrite'})
    def test_exact_revision_readback_mismatch_blocks_retirement(self):
        api=Api();s=self.store(api);s.read=lambda *a:b'tampered'
        with self.assertRaises(PersistenceError):s.publish({'metaculus/test/context.parquet':b'actual'})
    def test_acceptance_isolated_from_live_state_and_restores_budget(self):
        store=MemoryStore();store.publish({'metaculus/_control/checkpoint.json':b'production-owner-unchanged'})
        with tempfile.TemporaryDirectory() as root:result=storage(store,Path(root))
        self.assertEqual(result['status'],'storage_acceptance_passed');self.assertEqual(result['submissions'],0)
        self.assertTrue(result['local_payloads_retired']);self.assertTrue(result['budget_restored'])
        self.assertEqual(store.read('metaculus/_control/checkpoint.json'),b'production-owner-unchanged')
    def test_resume_reuses_exact_snapshot_without_collection(self):
        store=MemoryStore();pre='metaculus/_acceptance/'+'a'*32;p=post()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=initial_state('full-acceptance-'+'a'*32)
            task=dict(id='100',post_id=str(p['id']),post=p,request=live_request(p,p['question']),rule_identity=rule_identity(p,p['question']),
                run_id='a'*32,started_at=now(),test_only=True,status='sealed')
            cp=RuntimeCheckpoint(store,root/'original',key=pre+'/checkpoint.json');store.publish({cp.key:dumps(state)});cp.restore()
            package=sealed(task,cp.root/'tasks/100/capture');state['tasks']['100']=task
            value=context(task,package,task['snapshot_id']);raw=product('context',value)
            cp.save(state,extra={pre+'/context.parquet':raw,pre+'/manifest.json':dumps(manifest(task,{'context.parquet':raw}))})
            seen=[]
            def analyzed(value,*args):seen.append(value['snapshot_id']);return dict(schema='fixture-analysis',snapshot_id=value['snapshot_id'])
            with patch('metaculus_agent.acceptance.analyze',side_effect=analyzed),patch('metaculus_agent.acceptance.collect',side_effect=AssertionError('recollection forbidden')):
                result=resume(store,root/'resume',pre)
            self.assertEqual(seen,[task['snapshot_id']]);self.assertEqual(result['new_search_requests'],0);self.assertEqual(result['new_fetch_requests'],0)
            self.assertEqual(result['context_sha256'],sha(raw));self.assertTrue(result['local_payloads_retired']);self.assertEqual(result['submissions'],0)

if __name__=='__main__':unittest.main()
