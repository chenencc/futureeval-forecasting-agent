import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
import requests
from huggingface_hub.errors import HfHubHTTPError
from metaculus_agent.storage import Store,Conflict,PersistenceError

class Api:
    def __init__(self,private=True):self.private=private;self.head='a';self.files={'a':{'metaculus/_control/checkpoint.json':b'owner1'}};self.conflict=False;self.calls=0
    def repo_info(self,*a,**kw):return SimpleNamespace(private=self.private,sha=self.head)
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
    def test_exact_revision_readback_mismatch_blocks_retirement(self):
        api=Api();s=self.store(api);s.read=lambda *a:b'tampered'
        with self.assertRaises(PersistenceError):s.publish({'metaculus/test/context.parquet':b'actual'})

if __name__=='__main__':unittest.main()
