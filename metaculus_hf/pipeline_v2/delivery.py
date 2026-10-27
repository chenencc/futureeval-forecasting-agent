"""Independent immutable delivery staging. Never deletes a live repository file."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import tempfile
from .contracts import canonical, now
from .repository import atomic_bytes, ensure, file_lock


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class Delivery:
    """Owns sealed delivery copies only; receipts survive payload retirement.

    A per-artifact lock serializes upload/verify/delete. There is deliberately no
    call into the recorder, provider ledger, or question lifecycle scheduler.
    """
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def paths(self, key):
        p = PurePosixPath(key)
        ensure(not p.is_absolute() and '..' not in p.parts and '\\' not in key,
               'UNSAFE_DELIVERY_PATH')
        ensure(len(p.parts)>=3 and p.parts[0] in {'polymarket', 'metaculus'} and
               ((len(p.parts)==3 and p.parts[2]=='intelligence.parquet') or
                (len(p.parts)>=4 and p.parts[2] in {'intelligence','orderbook','trades'})), 'INVALID_DELIVERY_KEY')
        identity = hashlib.sha256(key.encode()).hexdigest()
        path = self.root / 'payloads' / p
        ensure(path.resolve().is_relative_to(self.root / 'payloads'), 'UNSAFE_DELIVERY_PATH')
        return path, self.root / 'receipts' / (identity + '.json'), self.root / 'locks' / identity

    def stage(self, key, data, *, metadata=None):
        path, receipt, lock = self.paths(key)
        expected = hashlib.sha256(data).hexdigest()
        with file_lock(lock):
            self._index(key)
            if receipt.exists():
                old = json.loads(receipt.read_text())
                ensure(old['sha256'] == expected and old['key'] == key, 'IMMUTABLE_DELIVERY_CONFLICT')
                ensure(all(old.get(k)==v for k,v in (metadata or {}).items()),'IMMUTABLE_DELIVERY_METADATA')
                return old
            if path.exists():
                ensure(sha(path) == expected, 'IMMUTABLE_DELIVERY_CONFLICT')
            else:
                atomic_bytes(path, data)
            row = dict(key=key, sha256=expected, bytes=len(data), state='sealed', sealed_at=now())
            ensure(not set(row).intersection(metadata or {}),'DELIVERY_RESERVED_METADATA')
            row.update(metadata or {})
            atomic_bytes(receipt, canonical(row).encode())
            return row

    def _index(self,key):
        qid=':'.join(key.split('/')[:2]);path=self.root/'indices'/(hashlib.sha256(qid.encode()).hexdigest()+'.json')
        with file_lock(path.with_suffix('.lock')):
            keys=json.loads(path.read_text()) if path.exists() else []
            if key not in keys:
                atomic_bytes(path,canonical(sorted([*keys,key])).encode())

    def receipts_for(self,qid):
        index=self.root/'indices'/(hashlib.sha256(qid.encode()).hexdigest()+'.json')
        if not index.exists():return []
        out=[]
        for key in json.loads(index.read_text()):
            path=self.paths(key)[1]
            if path.exists():out.append(json.loads(path.read_text()))
        return out

    def publish(self, key, transport, *, delete=False):
        path, receipt, lock = self.paths(key)
        with file_lock(lock):
            row = json.loads(receipt.read_text())
            ensure(row['key'] == key, 'DELIVERY_IDENTITY_MISMATCH')
            if row.get('remote_repo') is not None:
                ensure(row['remote_repo'] == transport.repo_id, 'REMOTE_REPOSITORY_CHANGED')
            if row['state'] == 'retired':
                return row
            if path.exists():
                ensure(sha(path) == row['sha256'], 'LOCAL_DELIVERY_CHANGED')
            else:
                ensure(row['state'] == 'verified', 'DELIVERY_PAYLOAD_MISSING')
            if row['state'] != 'verified':
                revision = row.get('remote_commit')
                if not revision:
                    revision = transport.upload(key, path)
                    row.update(remote_commit=revision, remote_repo=transport.repo_id, state='uploaded')
                    atomic_bytes(receipt, canonical(row).encode())
                with tempfile.TemporaryDirectory(dir=self.root) as folder:
                    downloaded = transport.download(key, revision, Path(folder))
                    ensure(sha(downloaded) == row['sha256'] and Path(downloaded).stat().st_size == row['bytes'],
                           'REMOTE_DELIVERY_MISMATCH')
                row.update(state='verified', verified_at=now())
                atomic_bytes(receipt, canonical(row).encode())
            if delete:
                # Only our immutable staging copy: never the source question/book.
                if path.exists():
                    ensure(sha(path) == row['sha256'], 'LOCAL_DELIVERY_CHANGED')
                    path.unlink()
                row.update(state='retired', retired_at=now())
                atomic_bytes(receipt, canonical(row).encode())
            return row


class HuggingFaceTransport:
    def __init__(self, repo_id, token):
        from huggingface_hub import HfApi
        self.repo_id, self.token = repo_id, token
        self.api = HfApi(token=token)
        ensure(self.api.repo_info(repo_id, repo_type='dataset').private, 'PRIVATE_DATASET_REQUIRED')

    def upload(self, key, path):
        from huggingface_hub import CommitOperationAdd
        info = self.api.repo_info(self.repo_id, repo_type='dataset')
        ensure(info.private, 'PRIVATE_DATASET_REQUIRED')
        return self.api.create_commit(self.repo_id, repo_type='dataset', parent_commit=info.sha,
            operations=[CommitOperationAdd(path_in_repo=key, path_or_fileobj=str(path))],
            commit_message='Publish sealed forecast artifact', num_threads=1).oid

    def download(self, key, revision, directory):
        from huggingface_hub import hf_hub_download
        ensure(self.api.repo_info(self.repo_id, repo_type='dataset').private, 'PRIVATE_DATASET_REQUIRED')
        return hf_hub_download(self.repo_id, key, repo_type='dataset', revision=revision,
            token=self.token, local_dir=str(directory), force_download=True)


def parquet_payload(payload):
    from .parquet_store import arrow
    import pyarrow.parquet as pq
    pa = arrow()
    table = pa.table({'payload': [canonical(payload)]})
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression='zstd', write_page_checksum=True)
    return sink.getvalue().to_pybytes()


def stage_intelligence(repo, delivery, question_id, *, terminal_failures=None):
    """Snapshot readiness is independent of one-hour book completion."""
    record = repo.get_question(question_id)
    prefix = question_id.replace(':', '/', 1) + '/intelligence/'
    staged = []
    for snap in record['evidence']['snapshots']:
        payload = repo.read_snapshot(question_id, snap['snapshot_id'])
        payload['searches'] = [s for s in record['searches'] if s['run_id'] == snap['acquired_by_run_id']]
        staged.append(delivery.stage(prefix + 'snapshots/' + snap['snapshot_id'] + '.parquet', parquet_payload(payload)))
    for run in record['runs']:
        failure=(terminal_failures or {}).get(run['run_id'])
        if run['selection'] is not None or failure:
            payload = {k: run[k] for k in ('run_id', 'snapshot_id', 'results', 'selection')}
            payload['question_id'] = question_id
            payload['status']='succeeded' if run['selection'] is not None else 'failed'
            payload['error_code']=None if run['selection'] is not None else failure
            staged.append(delivery.stage(prefix + 'forecasts/' + run['run_id'] + '.parquet', parquet_payload(payload)))
    return staged


def stage_books(repo, delivery, question_id):
    from .books import verify
    record = repo.get_runtime(question_id)
    staged = []
    for session in (record['orderbook'] or {}).get('sessions', []):
        if session['state'] in {'starting', 'recording'}:
            continue
        for segment in session['segments']:
            path = (repo.root / segment['path']).resolve()
            ensure(path.is_relative_to(repo.root), 'UNSAFE_BOOK_PATH')
            summary = verify(path)
            ensure(summary['file_sha256'] == segment['sha256'], 'BOOK_DELIVERY_HASH_MISMATCH')
            key = question_id.replace(':', '/', 1) + '/orderbook/' + session['recording_id'] + '/' + str(segment['sequence']) + '.parquet'
            staged.append(delivery.stage(key, path.read_bytes()))
    return staged
