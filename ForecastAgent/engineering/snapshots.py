"""Read original immutable batches through query-only databases, never Web text."""
from datetime import datetime
from contextlib import contextmanager
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .common import digest, instant, read
from .contracts import validate


@contextmanager
def open_readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def inventory(domain, database):
    with open_readonly(database) as db:
        if domain == 'competition':
            return [dict(r) for r in db.execute('SELECT id AS batch_id,question_key AS task_key,role,observed_at FROM bundles ORDER BY question_key,observed_at,id')]
        if domain == 'market':
            return [dict(r) for r in db.execute('SELECT id AS batch_id,market_key AS task_key,material_status AS role,observed_at FROM web_packets ORDER BY market_key,observed_at,id')]
        raise ValueError('Unknown snapshot domain')


def bytes_object(root, sha):
    if not re.fullmatch('[a-f0-9]{64}', sha):
        raise ValueError('Invalid original object hash')
    root = Path(root).resolve()
    data = root.parent.parent
    for parent in (root / 'objects', data / 'blobs/sha256'):
        path = parent / sha[:2] / sha
        if path.is_file():
            if not path.resolve().is_relative_to(parent.resolve()) or path.stat().st_size > 64 * 1024**2:
                raise ValueError('Unsafe or oversized original object')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError('Original object hash differs')
            return raw
    raise FileNotFoundError('Original object is not synchronized')


def resolve(domain, database, batch_id, task_key):
    """Explicit batch IDs are mandatory; no latest outcome/forecast join is used."""
    root = Path(database).resolve().parent
    with open_readonly(database) as db:
        if domain == 'competition':
            batch = db.execute('SELECT * FROM bundles WHERE id=? AND question_key=?', (batch_id, task_key)).fetchone()
            if not batch:
                raise KeyError('Unknown frozen competition batch')
            refs = list(db.execute('SELECT f.path,o.* FROM file_refs f JOIN native_objects o ON o.sha256=f.sha256 '
                                   'WHERE f.question_key=? AND f.source_id=?', (task_key, batch['source_id'])))
            suffix = batch['role'].removeprefix('local:').replace('\\', '/')
            for row in refs:
                if not row['path'].replace('\\', '/').endswith(suffix):
                    continue
                if row['size_bytes'] > 64 * 1024**2:
                    raise ValueError('Original bundle exceeds reading bound')
                # Bounded decompression prevents corrupt archive expansion.
                import zlib
                if row['codec'] == 'gzip':
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    raw = decoder.decompress(row['bytes'], 64 * 1024**2 + 1)
                    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                        raise ValueError('Invalid bounded gzip object')
                elif row['codec'] == 'raw':
                    raw = row['bytes']
                else:
                    raise ValueError('Unknown native object codec')
                if len(raw) != row['size_bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
                    raise ValueError('Native competition byte hash differs')
                bundle = json.loads(raw)
                if digest(bundle) == batch['bundle_sha256']:
                    return bundle, row['sha256'], batch['bundle_sha256']
            raise FileNotFoundError('Exact competition original is not synchronized')
        if domain != 'market':
            raise ValueError('Unknown snapshot domain')
        batch = db.execute('SELECT * FROM web_packets WHERE id=? AND market_key=?', (batch_id, task_key)).fetchone()
        if not batch:
            raise KeyError('Unknown frozen market batch')
        materials = json.loads(batch['payload']).get('materials')
        if not materials:
            raise FileNotFoundError('Batch has no locally bound native material')
        proof = materials['provenance']
        expected = materials['material_sha256']
        if proof['kind'] == 'verified_native_objects':
            prefix = 'tasks/' + digest(task_key) + '/' + proof['material_path']
            for row in db.execute('SELECT sha256 FROM material_refs WHERE market_key=? AND path=?', (task_key, prefix)):
                try:
                    raw = bytes_object(root, row['sha256'])
                except FileNotFoundError:
                    continue
                bundle = json.loads(raw)
                if digest(bundle) == expected:
                    return bundle, row['sha256'], expected
        elif proof['kind'] == 'verified_local_revision':
            for row in db.execute('SELECT data FROM revisions WHERE market_key=?', (task_key,)):
                revision = json.loads(row['data'])
                if revision['result_sha256'] != proof['result_sha256']:
                    continue
                result = Path(revision['result_file']).resolve()
                allowed = root.parent / 'revisions'
                if not result.is_relative_to(allowed.resolve()):
                    raise ValueError('Revision is outside its declared archive')
                if digest(read(result)) != revision['result_sha256']:
                    raise ValueError('Revision identity changed')
                path = result.parent / 'frozen-input.json'
                bundle = read(path)
                if digest(bundle) != expected:
                    raise ValueError('Revision material changed')
                return bundle, hashlib.sha256(path.read_bytes()).hexdigest(), expected
        else:
            raise ValueError('Unsupported native material binding')
        raise FileNotFoundError('Exact frozen market original is not synchronized')


def packet(domain, database, batch_id, task_key, *, as_of_utc=None):
    bundle, archive_sha, bundle_sha = resolve(domain, database, batch_id, task_key)
    request = bundle['request']
    if domain == 'market':
        if request.get('market_research_key') != task_key:
            raise ValueError('Market request belongs to another question')
        platform = task_key.split(':', 1)[0]
    else:
        if 'metaculus:' + str(request.get('id')) != task_key:
            raise ValueError('Competition request belongs to another question')
        platform = 'metaculus'
    # Deliberate allowlist: resolutions, prices, model answers, graphs, transcripts
    # and later supplements never leak from the raw archive into this input.
    kind = request.get('type') or request.get('question_type') or 'binary'
    kind = {'categorical': 'multiple_choice', 'continuous': 'numeric'}.get(kind, kind)
    q = {'key': task_key, 'platform': platform, 'kind': kind,
         'title': request.get('question') or request.get('title') or '',
         'rules': request.get('resolution_criteria') or '', 'fine_print': request.get('fine_print') or '',
         'options': request.get('options') or request.get('all_options_ever') or [],
         'scale': request.get('scaling') or request.get('scale') or {},
         'observed_at': request.get('as_of_utc') or bundle.get('created_at')}
    if q['observed_at']:
        try:
            instant(q['observed_at'])
        except ValueError:
            q['observed_at'] = None
    gaps = [{'code': 'native_gap_reference', 'record_sha256': digest(gap)}
            for gap in list(bundle.get('gaps') or []) + list(bundle.get('capture_gaps') or [])]
    sources = []
    for url, page in bundle.get('pages', {}).items():
        text = page.get('content')
        if not isinstance(text, str) or not text.strip() or page.get('body_diagnostics', {}).get('usable_text') is False:
            gaps.append({'code': 'unreadable_body', 'url': url})
            continue
        if re.search(r'access denied|verify(?:ing)? (?:you are human|your browser)|enable javascript', text[:1000], re.I) and len(text) < 400:
            gaps.append({'code': 'body_shell', 'url': url})
            continue
        sha = hashlib.sha256(text.encode()).hexdigest()
        if page.get('content_sha256') and page['content_sha256'] != sha:
            raise ValueError('Original body hash differs')
        captured = next((page.get(k) for k in ('retrieved_at_utc', 'captured_at_utc', 'fetched_at', 'retrieved_at') if page.get(k)), None)
        if captured:
            try:
                instant(captured)
            except ValueError:
                captured = None
        if as_of_utc and (not captured or instant(captured) > instant(as_of_utc)):
            gaps.append({'code': 'after_cutoff_or_unknown_capture', 'url': url})
            continue
        # Page publication clocks are preserved as unknown unless explicitly saved.
        published = page.get('published_at') or page.get('published_at_utc')
        try:
            if published:
                instant(published)
        except ValueError:
            published = None
        sources.append({'id': digest([url, sha]), 'url': url, 'body_sha256': sha, 'text': text,
                        'captured_at': captured, 'published_at': published, 'role': 'original_body'})
    if not sources:
        gaps.append({'code': 'no_readable_originals'})
    result = {'schema': 'forecast-evidence-packet-v1', 'case_id': domain + ':' + task_key + ':' + batch_id,
              'question': q, 'sources': sources, 'source_bundle_sha256': bundle_sha,
              'archive_byte_sha256': archive_sha, 'native_request_sha256': digest(request),
              'cutoff_policy': 'strict_as_of' if as_of_utc else 'capture_time_unbounded',
              'as_of_utc': as_of_utc, 'gaps': gaps,
              'input_role': 'sanitized_acquisition_snapshot_not_exact_legacy_model_input'}
    return validate('packet', result)
