"""Synthetic originals with deliberate answer/price traps outside the allowlist."""
import gzip
import hashlib
from pathlib import Path

from ForecastAgent.engineering.common import digest, encoded
from ForecastAgent.competition_store import store

CLOCK = '2026-10-14T12:00:00+00:00'


def bundle(kind='binary', ident='123'):
    text = 'Official pre-event observations and a preserved numeric table. ' * 10
    return {'request': {'id': ident, 'question': 'Will the policy pass?', 'type': kind,
                        'resolution_criteria': 'Resolves Yes if enacted before the stated deadline.',
                        'fine_print': '', 'as_of_utc': CLOCK,
                        'options': ['A', 'B'] if kind == 'multiple_choice' else [],
                        'scaling': {'range_min': 0, 'range_max': 100} if kind in ('numeric', 'discrete', 'date') else {},
                        'resolution': 'FORBIDDEN_RESOLUTION'},
            'pages': {'https://example.org/original': {'content': text, 'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
                                                       'retrieved_at_utc': CLOCK, 'body_diagnostics': {'usable_text': True}}},
            'resolution': 'FORBIDDEN_RESOLUTION', 'forecast': 'FORBIDDEN_FORECAST',
            'transcript': 'FORBIDDEN_TRANSCRIPT', 'market_snapshots': ['FORBIDDEN_PRICE'],
            'gaps': [{'condition': 'A still-future event is not an acquisition failure'}]}


def packet(kind='binary'):
    value = bundle(kind)
    url, page = next(iter(value['pages'].items()))
    return {'schema': 'forecast-evidence-packet-v1', 'case_id': 'fixture:' + kind,
            'question': {'key': 'metaculus:123', 'platform': 'metaculus', 'kind': kind,
                         'title': value['request']['question'], 'rules': value['request']['resolution_criteria'],
                         'fine_print': '', 'options': value['request']['options'], 'scale': value['request']['scaling'], 'observed_at': CLOCK},
            'sources': [{'id': 'source1', 'url': url, 'body_sha256': page['content_sha256'], 'text': page['content'],
                         'captured_at': CLOCK, 'published_at': None, 'role': 'original_body'}],
            'source_bundle_sha256': digest(value), 'archive_byte_sha256': digest(value), 'native_request_sha256': digest(value['request']),
            'cutoff_policy': 'strict_as_of', 'as_of_utc': CLOCK, 'gaps': [],
            'input_role': 'sanitized_acquisition_snapshot_not_exact_legacy_model_input'}


def competition(root, kind='binary'):
    root = Path(root)
    path = root / 'competition.sqlite3'
    db = store.connect(path)
    value = bundle(kind)
    raw = encoded(value)
    sha = hashlib.sha256(raw).hexdigest()
    source = 'synthetic-original'
    with db:
        bid = store.bundle(db, '123', value, 'analysis-input.json', CLOCK, source)
        store.save(db, 'native_objects', (sha, gzip.compress(raw, mtime=0), len(raw), 'gzip'))
        store.save(db, 'file_refs', (digest(['fixture', sha]), source, 'metaculus:123', 'tasks/123/analysis-input.json', sha, len(raw), 'verified_local_object', '{}'))
    db.close()
    return path, bid


def routes():
    return [{'id': 'a', 'model': 'fixture-only', 'prompt': 'Use only frozen original evidence.', 'config': {'temperature': 0}},
            {'id': 'b', 'model': 'fixture-only', 'prompt': 'A second frozen prompt.', 'config': {'temperature': 0}}]
