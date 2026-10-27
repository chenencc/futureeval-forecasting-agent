"""Exact-revision private storage; never uses GitHub artifacts."""
import hashlib,json,re,tempfile
from pathlib import Path,PurePosixPath
from pipeline_v2.contracts import canonical

def sha(data):return hashlib.sha256(data).hexdigest()
def safe(key):
    p=PurePosixPath(key)
    if not key.startswith('metaculus/') or p.is_absolute() or '..' in p.parts or '\\' in key:raise ValueError('UNSAFE_HF_KEY')
    return key
def dumps(value):return canonical(value).encode()

class Conflict(RuntimeError):pass
class PersistenceError(RuntimeError):pass
class Store:
    def __init__(self,repo_id,token,*,api=None):
        from huggingface_hub import HfApi
        self.repo_id,self.token=repo_id,token;self.api=api or HfApi(token=token)
        info=self.api.repo_info(repo_id,repo_type='dataset')
        if info.private is not True:raise ValueError('PRIVATE_DATASET_REQUIRED')
        self.head=info.sha;self.guards={}
    def read(self,key,revision=None):
        from huggingface_hub import hf_hub_download
        with tempfile.TemporaryDirectory() as folder:
            p=hf_hub_download(self.repo_id,safe(key),repo_type='dataset',revision=revision or self.head,
                              token=self.token,local_dir=folder,force_download=True)
            return Path(p).read_bytes()
    def exists(self,key):return self.api.file_exists(self.repo_id,safe(key),repo_type='dataset',revision=self.head)
    def publish(self,updates):
        """CAS. A conflict requires reload/reconciliation, never blind retry."""
        from huggingface_hub import CommitOperationAdd
        from huggingface_hub.errors import HfHubHTTPError
        result=None
        for attempt in range(3):
            try:
                result=self.api.create_commit(self.repo_id,repo_type='dataset',parent_commit=self.head,
                    commit_message='Metaculus durable checkpoint',operations=[CommitOperationAdd(path_in_repo=safe(k),path_or_fileobj=v) for k,v in updates.items()])
                break
            except HfHubHTTPError as exc:
                if exc.response is None or exc.response.status_code not in (409,412):raise PersistenceError('HF_COMMIT_FAILED') from None
                latest=self.api.repo_info(self.repo_id,repo_type='dataset').sha
                if not self.guards:raise Conflict('HF_HEAD_CHANGED') from None
                for key,old in self.guards.items():
                    if old is None:
                        if self.api.file_exists(self.repo_id,safe(key),repo_type='dataset',revision=latest):raise Conflict('HF_WRITER_CHANGED') from None
                    elif self.read(key,latest)!=old:raise Conflict('HF_WRITER_CHANGED') from None
                self.head=latest
        if result is None:raise Conflict('HF_HEAD_BUSY')
        revision=result.oid
        for key,body in updates.items():
            if self.read(key,revision)!=body:raise PersistenceError('HF_READBACK_MISMATCH')
        self.head=revision
        for key in self.guards:
            if key in updates:self.guards[key]=updates[key]
        return dict(revision=revision,files={k:dict(sha256=sha(v),bytes=len(v)) for k,v in updates.items()},verified=True)

class RuntimeCheckpoint:
    """Content-addressed active files; compact control restored first."""
    def __init__(self,store,root,*,key='metaculus/_control/checkpoint.json'):
        self.store,self.root,self.key=store,Path(root).resolve(),safe(key)
        self.state=None;self.objects=set()
    def restore(self):
        if not self.store.exists(self.key):raise RuntimeError('MIGRATION_CHECKPOINT_REQUIRED')
        raw=self.store.read(self.key);state=json.loads(raw);self._validate(state)
        if hasattr(self.store,'guards'):self.store.guards[self.key]=raw
        self.state=state;self.objects=set(state.get('runtime',{}).values())
        for name,hashed in state.get('runtime',{}).items():
            target=self._target(name);raw=self.store.read('metaculus/_control/objects/'+hashed)
            if sha(raw)!=hashed:raise RuntimeError('CHECKPOINT_OBJECT_HASH')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        return state
    def _target(self,name):
        target=(self.root/name).resolve()
        if not target.is_relative_to(self.root) or Path(name).is_absolute() or '\\' in name or '..' in PurePosixPath(name).parts or PurePosixPath(name).parts[0] not in ('tasks','budgets'):raise ValueError('UNSAFE_RUNTIME_PATH')
        return target
    @staticmethod
    def _validate(state):
        if state.get('schema')!='metaculus-checkpoint/1' or not isinstance(state.get('tasks'),dict):raise ValueError('CHECKPOINT_SCHEMA')
        for hashed in state.get('runtime',{}).values():
            if not re.fullmatch('[0-9a-f]{64}',hashed):raise ValueError('CHECKPOINT_HASH')
    def save(self,state,*,extra=None):
        updates=dict(extra or {});runtime={}
        # Only application-owned task files and budgets; never browser auth/.env.
        for folder in ['tasks','budgets']:
            base=self.root/folder
            if not base.exists():continue
            for path in sorted(base.rglob('*')):
                if not path.is_file() or path.is_symlink() or path.suffix not in {'.json','.parquet','.jsonl'} or any(x in path.parts for x in ('.locks','browser')):continue
                raw=path.read_bytes();hashed=sha(raw);runtime[path.relative_to(self.root).as_posix()]=hashed
                if hashed not in self.objects:updates['metaculus/_control/objects/'+hashed]=raw
        value={**state,'runtime':runtime};self._validate(value)
        updates[self.key]=dumps(value);receipt=self.store.publish(updates)
        self.objects.update(runtime.values());self.state=value
        return receipt
