"""Exact-revision private storage; never uses GitHub artifacts."""
import hashlib,json,re,tempfile,os,time
from pathlib import Path,PurePosixPath
from pipeline_v2.contracts import canonical

def sha(data):return hashlib.sha256(data).hexdigest()
def safe(key):
    p=PurePosixPath(key)
    if not key.startswith('metaculus/') or p.is_absolute() or '..' in p.parts or '\\' in key:raise ValueError('UNSAFE_HF_KEY')
    return key
def dumps(value):return canonical(value).encode()

class Conflict(RuntimeError):pass
class PersistenceError(RuntimeError):
    def __init__(self,code,*,phase=None,status=None,retry_after=None):
        super().__init__(code);self.code=code;self.phase=phase;self.http_status=status;self.retry_after=retry_after

class QuestionBatch:
    """Local checkpoints; one remote commit at the question boundary.

    Local staging is NOT a cloud receipt. A killed runner can lose its unflushed
    question; authenticated platform readback still precedes a later submission.
    """
    batched=True
    def __init__(self,backend,root,*,autoflush=True):
        self.backend,self.root,self.autoflush=backend,Path(root),autoflush
        self.root.mkdir(parents=True,exist_ok=True)
        self.pending={};self.guards=dict(getattr(backend,'guards',{}));self.base_guards=None
        self.successful_commits=0
        self.discarded_objects=set()
    @property
    def head(self):return self.backend.head
    def is_staged(self,key):return key in self.pending
    def exists(self,key):return key in self.pending or self.backend.exists(key)
    def read(self,key,revision=None):
        if revision is None and key in self.pending:return self.pending[key].read_bytes()
        return self.backend.read(key,revision)
    def check_owner(self):
        latest=self.backend.api.repo_info(self.backend.repo_id,repo_type='dataset').sha if hasattr(self.backend,'api') else self.backend.head
        for key,old in (self.base_guards if self.base_guards is not None else self.guards).items():
            if old is None:
                exists=self.backend.api.file_exists(self.backend.repo_id,key,repo_type='dataset',revision=latest) if hasattr(self.backend,'api') else self.backend.exists(key)
                if exists:raise Conflict('HF_WRITER_CHANGED')
            elif self.backend.read(key,latest)!=old:raise Conflict('HF_WRITER_CHANGED')
    def publish(self,updates):
        if self.base_guards is None:self.base_guards=dict(self.guards)
        for key,body in updates.items():
            safe(key);path=self.root/sha(key.encode());tmp=path.with_suffix('.tmp')
            with tmp.open('wb') as handle:handle.write(body);handle.flush();os.fsync(handle.fileno())
            os.replace(tmp,path);self.pending[key]=path
        return dict(revision=None,verified=False,local_checkpoint=True,
                    files={k:dict(sha256=sha(v),bytes=len(v)) for k,v in updates.items()})
    def flush(self):
        if not self.pending:return dict(revision=self.head,verified=True,remote_commit=False)
        updates={k:p.read_bytes() for k,p in self.pending.items()};referenced=set()
        for key,raw in updates.items():
            if '/_control/objects/' not in key and key.endswith('.json'):
                obj=json.loads(raw)
                if isinstance(obj,dict) and obj.get('schema') in ('metaculus-checkpoint/1','metaculus-question-runtime/1'):
                    referenced.update(obj.get('runtime',{}).values())
        self.discarded_objects={k.rsplit('/',1)[1] for k in updates if '/_control/objects/' in k and k.rsplit('/',1)[1] not in referenced}
        updates={k:v for k,v in updates.items() if '/_control/objects/' not in k or k.rsplit('/',1)[1] in referenced}
        self.backend.guards=dict(self.base_guards or {})
        receipt=self.backend.publish(updates)
        if not receipt.get('verified'):raise PersistenceError('HF_BATCH_NOT_VERIFIED')
        self.successful_commits+=1
        for path in self.pending.values():path.unlink(missing_ok=True)
        self.pending.clear();self.base_guards=None
        self.guards=dict(getattr(self.backend,'guards',{}))
        return {**receipt,'remote_commit':True,'checkpoint_policy':'one-commit-per-question'}
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
        result=None;last_error=None;reconciled=False
        def guard(latest):
            if not self.guards:raise Conflict('HF_HEAD_CHANGED')
            for key,old in self.guards.items():
                if old is None:
                    if self.api.file_exists(self.repo_id,key,repo_type='dataset',revision=latest):raise Conflict('HF_WRITER_CHANGED')
                elif self.read(key,latest)!=old:raise Conflict('HF_WRITER_CHANGED')
        for attempt in range(3):
            try:
                result=self.api.create_commit(self.repo_id,repo_type='dataset',parent_commit=self.head,
                    commit_message='Metaculus complete question batch',operations=[CommitOperationAdd(path_in_repo=safe(k),path_or_fileobj=v) for k,v in updates.items()])
                break
            except HfHubHTTPError as exc:
                status=exc.response.status_code if exc.response is not None else None
                delay=(exc.response.headers.get('Retry-After') if exc.response is not None else None)
                delay=int(delay) if delay and delay.isdecimal() else 2**attempt
                last_error=PersistenceError('HF_COMMIT_FAILED',phase='commit',status=status,retry_after=delay)
                if status not in (409,412,429,500,502,503,504) and status is not None:raise last_error from None
                latest=self.api.repo_info(self.repo_id,repo_type='dataset').sha
                # A lost acknowledgement must not cause a second successful commit.
                if all(self.api.file_exists(self.repo_id,k,repo_type='dataset',revision=latest) and self.read(k,latest)==v for k,v in updates.items()):
                    from types import SimpleNamespace
                    result=SimpleNamespace(oid=latest);reconciled=True;break
                guard(latest)
                self.head=latest
                if status not in (409,412):
                    if attempt==2 or delay>120:raise last_error from None
                    time.sleep(min(delay,120))
        if result is None:raise last_error or Conflict('HF_HEAD_BUSY')
        revision=result.oid
        for key,body in updates.items():
            if self.read(key,revision)!=body:raise PersistenceError('HF_READBACK_MISMATCH',phase='exact_revision_readback')
        self.head=revision
        for key in self.guards:
            if key in updates:self.guards[key]=updates[key]
        return dict(revision=revision,files={k:dict(sha256=sha(v),bytes=len(v)) for k,v in updates.items()},verified=True,reconciled_commit=reconciled)

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
        for task in state['tasks'].values():
            for kind in ('context','analysis'):
                if task.get(kind+'_key') and task.get(kind+'_revision') is None and not getattr(self.store,'is_staged',lambda key:False)(task[kind+'_key']):
                    task[kind+'_revision']=self.store.head
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
                relative=path.relative_to(self.root).as_posix()
                if any(relative.startswith('tasks/'+str(t['id'])+'/') and t.get('payloads_archived') for t in state['tasks'].values()):continue
                raw=path.read_bytes();hashed=sha(raw);runtime[path.relative_to(self.root).as_posix()]=hashed
                if hashed not in self.objects:updates['metaculus/_control/objects/'+hashed]=raw
        value={**state,'runtime':runtime};self._validate(value)
        updates[self.key]=dumps(value);receipt=self.store.publish(updates)
        self.objects.update(runtime.values());self.state=value
        return receipt
