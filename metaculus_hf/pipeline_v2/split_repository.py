"""Control and book commits never open independently disposable evidence."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
from .contracts import canonical, digest, now, validate_record
from .repository import Repository, atomic_bytes, ensure, validate_transition
from .delivery import parquet_payload, sha


def index_read(path):
    row = json.loads(path.read_text(encoding='utf-8'))
    ensure(row.get('schema') == 'split-question/2', 'SPLIT_SCHEMA_MISMATCH')
    ensure(row['index_hash'] == digest({k:v for k,v in row.items() if k != 'index_hash'}), 'SPLIT_INDEX_HASH')
    relative = row['intelligence']['path']
    ensure((relative=='intelligence.parquet' or relative.startswith('intelligence/')) and '..' not in relative.split('/') and '\\' not in relative, 'UNSAFE_SPLIT_REFERENCE')
    ensure((path.parent/relative).resolve().is_relative_to(path.parent.resolve()), 'UNSAFE_SPLIT_REFERENCE')
    return row


def index_write(path, row, before_replace=None):
    row = deepcopy(row)
    row['index_hash'] = digest({k:v for k,v in row.items() if k != 'index_hash'})
    atomic_bytes(path, canonical(row).encode(), before_replace=before_replace)


@contextmanager
def body_file(repo, path, index):
    ref = index['intelligence'];local = path.parent/ref['path']
    if local.exists():
        ensure(sha(local) == ref['sha256'], 'SPLIT_HASH_MISMATCH')
        yield local
        return
    remote = ref.get('remote')
    ensure(remote is not None, 'INTELLIGENCE_UNAVAILABLE')
    from .delivery_worker import configured_transport
    transport = getattr(repo, 'transport', None) or configured_transport(repo.root)
    ensure(transport.repo_id == remote['remote_repo'], 'REMOTE_REPOSITORY_CHANGED')
    with tempfile.TemporaryDirectory(prefix='restore-', dir=repo.root) as folder:
        downloaded = transport.download(remote['key'], remote['remote_commit'], Path(folder))
        ensure(sha(downloaded) == remote['sha256'], 'SPLIT_HASH_MISMATCH')
        from .intelligence_package import load,validate_export
        validate_export(load(downloaded),ref,remote)
        yield downloaded


def read_full(repo, path):
    index = index_read(path)
    import pyarrow.parquet as pq
    with body_file(repo, path, index) as body:
        with pq.ParquetFile(body, page_checksum_verification=True) as file:
            rows = file.read(use_threads=False).to_pylist()
        ensure(len(rows) == 1, 'SPLIT_ROW_COUNT')
        from .intelligence_package import package_for_question
        payload = package_for_question(json.loads(rows[0]['payload']),index['control']['question']['id'])
    record = deepcopy(index['control'])
    ensure(payload['question_id'] == record['question']['id'] and payload['definitions'] == record['question']['versions'], 'SPLIT_IDENTITY_MISMATCH')
    record.update(searches=payload['searches'], evidence=payload['evidence'])
    record['record_hash'] = digest(record)
    return validate_record(record, repo.validator)


def write_full(repo, path, record, before_replace=None):
    payload = dict(schema='intelligence-package/1', question_id=record['question']['id'],
                   definitions=record['question']['versions'], searches=record['searches'], evidence=record['evidence'])
    data = parquet_payload(payload);hashed = hashlib.sha256(data).hexdigest()
    single=any(r['snapshot_id'] and r['execution_profile']['parameters'].get('intelligence_storage_layout')=='single-sealed-parquet-v1' for r in record['runs'])
    relative = 'intelligence.parquet' if single else 'intelligence/' + hashed + '.parquet';target = path.parent/relative
    previous = index_read(path) if path.exists() else None
    if target.exists():
        if single and previous and previous['intelligence']['path']!=relative:
            # A crash before index replacement may leave an unreferenced fixed
            # file. The committed index still owns its hashed body, so retry can
            # atomically replace this orphan without touching committed input.
            if sha(target)!=hashed:atomic_bytes(target,data)
        else:ensure(sha(target) == hashed, 'SEALED_INTELLIGENCE_CHANGED' if single else 'SPLIT_COMPONENT_COLLISION')
    else:atomic_bytes(target, data)
    control = {k:v for k,v in record.items() if k not in {'record_hash', 'searches', 'evidence'}}
    ref = dict(path=relative, sha256=hashed, bytes=len(data))
    if previous and previous['intelligence']['sha256'] == hashed:
        ref = previous['intelligence']
    index_write(path, dict(schema='split-question/2', control=control, intelligence=ref), before_replace)
    if previous and previous['intelligence']['path'] != relative:
        try:(path.parent/previous['intelligence']['path']).unlink(missing_ok=True)
        except OSError:pass


def runtime(repo, path):
    return deepcopy(index_read(path)['control'])


def book_commit(repo, path, update):
    row = index_read(path);old = row['control'];new = deepcopy(old)
    update(new)
    ensure({k:v for k,v in old.items() if k != 'orderbook'} == {k:v for k,v in new.items() if k != 'orderbook'}, 'BOOK_PATCH_SCOPE')
    def scaffold(value):return dict(value, searches=[], evidence=dict(texts={},sources=[],snapshots=[]))
    validate_transition(scaffold(old), scaffold(new))
    from jsonschema import Draft202012Validator, FormatChecker
    Draft202012Validator({'$defs':repo.contract['$defs'], **repo.contract['properties']['orderbook']}, format_checker=FormatChecker()).validate(new['orderbook'])
    new.update(revision=old['revision']+1, updated_at=now())
    row['control'] = new;index_write(path, row)
    return deepcopy(new)


class SplitRepository(Repository):
    """Explicit isolated backend; production opts in using control/storage.json."""
    split_new = True
