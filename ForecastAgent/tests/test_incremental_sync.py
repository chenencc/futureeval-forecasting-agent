"""Traffic, provenance and interruption tests without providers or network."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile, ZIP_DEFLATED

from ForecastAgent import local_sync
from ForecastAgent.incremental_sync import import_members, import_catalog, validate_expected, execute
from ForecastAgent.infrastructure.local_sync_export import export, PREFIX
from ForecastAgent.local_transfer import ArtifactZip, RangeFile, TrafficBudget, TransferDeferred

REPO = 'chenencc/futureeval-forecasting-agent'


def zip_bytes(folder, name, files):
    path = folder / (name + '.zip')
    with ZipFile(path, 'w', compression=ZIP_DEFLATED) as saved:
        for filename, data in files.items():
            saved.writestr(filename, data)
    return path.read_bytes()


def expected(files):
    return [{'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)} for name, data in files.items()]


class Client:
    def __init__(self, data, name='market-repricing-v2-worker-state', run='100', ident=1):
        self.data = {str(ident): data}
        self.metadata = {str(ident): {'id': ident, 'name': name, 'size_in_bytes': len(data),
            'digest': 'sha256:' + hashlib.sha256(data).hexdigest(), 'expired': False,
            'workflow_run': {'id': int(run)}}}
        self.requests = []
        self.fail_request = None

    def api(self, endpoint):
        if '/actions/artifacts/' in endpoint:
            return self.metadata[endpoint.rsplit('/', 1)[-1]]
        return {'artifacts': list(self.metadata.values())}

    def open_zip(self, root, repo, info, budget):
        ident = str(info['id'])
        def fetch(start, stop):
            budget.reserve(stop - start)
            self.requests.append((ident, start, stop))
            if len(self.requests) == self.fail_request:
                raise RuntimeError('Injected transfer interruption')
            result = self.data[ident][start:stop]
            budget.downloaded += len(result)
            return result
        return ArtifactZip(RangeFile(Path(root) / 'downloads' / ident, info, fetch, budget))


class RangeTests(TestCase):
    def test_resume_uses_exact_cached_bytes_after_interruption(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data = os.urandom(2500000)
            calls = []
            def fetch(start, stop):
                calls.append((start, stop))
                if len(calls) == 2:
                    raise RuntimeError('Interrupted')
                return data[start:stop]
            info = {'id': 1, 'size_in_bytes': len(data), 'digest': 'sha256:stable'}
            with self.assertRaises(RuntimeError):
                RangeFile(root, info, fetch).read()
            first_request = calls[0]
            restored = RangeFile(root, info, fetch)
            self.assertEqual(restored.read(), data)
            self.assertEqual(calls.count(first_request), 1)

    def test_changed_artifact_identity_rejects_old_ranges(self):
        with TemporaryDirectory() as directory:
            info = {'id': 1, 'size_in_bytes': 100, 'digest': 'a'}
            reader = RangeFile(directory, info, lambda a, b: b'x' * (b - a))
            reader.read(10)
            with self.assertRaises(ValueError):
                RangeFile(directory, info | {'digest': 'b'}, lambda a, b: b'y' * (b - a))

    def test_modified_cache_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            info = {'id': 1, 'size_in_bytes': 100, 'digest': 'a'}
            RangeFile(root, info, lambda a, b: b'x' * (b - a)).read(10)
            (root / '0-10.bin').write_bytes(b'y' * 10)
            with self.assertRaises(ValueError):
                RangeFile(root, info, lambda a, b: b'x' * (b - a)).read(10)

    def test_overlapping_cache_does_not_download_overlap(self):
        with TemporaryDirectory() as directory:
            calls = []
            def fetch(a, b):
                calls.append((a, b))
                return bytes(range(100))[a:b]
            reader = RangeFile(directory, {'id': 1, 'size_in_bytes': 100, 'digest': 'a'}, fetch)
            reader.seek(10)
            reader.read(10)
            reader.seek(0)
            self.assertEqual(reader.read(30), bytes(range(30)))
            self.assertEqual(calls, [(10, 20), (0, 10), (20, 30)])

    def test_budget_is_persistent_and_does_not_refund_failed_reservations(self):
        with TemporaryDirectory() as directory:
            clock = lambda: '2026-10-13T12:00:00+08:00'
            budget = TrafficBudget(directory, 10, 20, clock)
            budget.reserve(8)
            with self.assertRaises(TransferDeferred):
                TrafficBudget(directory, 10, 20, clock).reserve(3)
            self.assertEqual(TrafficBudget(directory, 10, 20, clock).snapshot()['reserved_bytes_today'], 8)
            next_day = TrafficBudget(directory, 10, 20, lambda: '2026-10-14T12:00:00+08:00')
            next_day.reserve(10)
            with self.assertRaises(TransferDeferred):
                TrafficBudget(directory, 10, 20, lambda: '2026-10-15T12:00:00+08:00').reserve(3)


class IncrementalTests(TestCase):
    def test_known_body_is_not_transferred_and_files_remain_readable(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            root = temp / 'local'
            body = os.urandom(2000000)
            files = {'tasks/123/raw.bin': body, 'state.json': b'{"tasks":{}}'}
            client = Client(zip_bytes(temp, 'source', files))
            local_sync.blob(root, body)
            budget = TrafficBudget(root, 10 * 1024 ** 2)
            with local_sync.connect(root) as db:
                report = import_members(root, db, client, REPO, client.metadata['1'], budget, expected(files))
                self.assertEqual(report['reused_files'], 1)
                self.assertEqual(report['fetched_files'], 1)
                self.assertEqual(db.execute('SELECT status,archive_sha256 FROM artifacts').fetchone(), ('files_imported', None))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM files').fetchone()[0], 2)
            self.assertLess(budget.downloaded, len(client.data['1']) // 10)
            self.assertFalse(report['original_zip_saved'])
            self.assertFalse((root / 'archives').exists())

    def test_member_hash_failure_never_claims_complete_archive(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            root = temp / 'local'
            files = {'a.json': b'{}'}
            client = Client(zip_bytes(temp, 'bad', files))
            declared = expected(files)
            declared[0]['sha256'] = '0' * 64
            with local_sync.connect(root) as db:
                with self.assertRaises(ValueError):
                    import_members(root, db, client, REPO, client.metadata['1'], TrafficBudget(root, 1000000), declared)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 0)

    def test_missing_or_additional_member_is_rejected(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            root = temp / 'local'
            files = {'a.json': b'{}', 'b.json': b'{}'}
            client = Client(zip_bytes(temp, 'extra', files))
            with local_sync.connect(root) as db:
                with self.assertRaises(ValueError):
                    import_members(root, db, client, REPO, client.metadata['1'], TrafficBudget(root, 1000000), expected({'a.json': b'{}'}))

    def test_traversal_and_duplicate_members_are_rejected(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            for name in ('../bad.json', 'C:/bad.json', 'a/../b.json'):
                data = zip_bytes(temp, 'unsafe', {name: b'{}'})
                client = Client(data)
                with self.assertRaises(ValueError):
                    client.open_zip(temp / 'local' / hashlib.sha256(name.encode()).hexdigest(), REPO,
                                    client.metadata['1'], TrafficBudget(temp, 1000000))
            with self.assertRaises(ValueError):
                validate_expected(expected({'../bad': b'{}'}))

    def test_fallback_sealed_inventory_preserves_sha_verification(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            root = temp / 'local'
            data = {'state.json': b'{"tasks":{}}', 'tasks/123/bundle.json': b'{"request":{"id":"123"}}'}
            inventory = json.dumps({'schema': 'test', 'files': {k: hashlib.sha256(v).hexdigest() for k, v in data.items()}}).encode()
            data['inventory.json'] = inventory
            client = Client(zip_bytes(temp, 'inventory', data))
            with local_sync.connect(root) as db:
                report = import_members(root, db, client, REPO, client.metadata['1'], TrafficBudget(root, 1000000))
                self.assertEqual(report['assurance'], 'publisher_sha256_and_member_crc')
                self.assertEqual(report['expected_files'], 3)
                self.assertEqual(db.execute('SELECT question_id FROM records WHERE kind="question"').fetchone()[0], '123')

    def test_catalog_restores_all_members_after_traffic_pause(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / 'source'
            source.mkdir()
            files = {'state.json': b'{"tasks":{}}', 'raw.bin': os.urandom(1500000)}
            for name, data in files.items():
                (source / name).write_bytes(data)
            export([('market-repricing-v2-worker-state', source, None)], temp / 'catalog', 'market-worker', run_id='100', repo=REPO)
            client = Client(zip_bytes(temp, 'source', files))
            catalog_data = zip_bytes(temp, 'catalog', {p.name: p.read_bytes() for p in (temp / 'catalog').iterdir()})
            client.data['2'] = catalog_data
            client.metadata['2'] = {'id': 2, 'name': PREFIX + 'market-worker', 'size_in_bytes': len(catalog_data),
                'digest': 'sha256:' + hashlib.sha256(catalog_data).hexdigest(), 'expired': False, 'workflow_run': {'id': 100}}
            root = temp / 'local'
            with local_sync.connect(root) as db:
                with self.assertRaises(TransferDeferred):
                    import_catalog(root, db, client, REPO, client.metadata['2'], TrafficBudget(root, 100000))
                self.assertTrue((root / 'sync-catalogs/market-worker/latest-summary.json').exists())
                self.assertEqual(db.execute('SELECT COUNT(*) FROM artifacts WHERE status="files_imported"').fetchone()[0], 0)
                first_ranges = list(client.requests)
                result = import_catalog(root, db, client, REPO, client.metadata['2'], TrafficBudget(root, 10000000))
                self.assertTrue(result['catalog_sources'])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM artifacts WHERE status="files_imported"').fetchone()[0], 2)
                self.assertEqual(len(client.requests), len(set(client.requests)))
                self.assertGreaterEqual(len(client.requests), len(first_ranges))

    def test_automatic_sync_defers_experiments_and_old_full_checkpoints(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with local_sync.connect(root) as db:
                for ident, run_id, name in [('1', '100', 'market-repricing-v2-worker-state'),
                                             ('2', '101', 'market-repricing-v2-worker-state'),
                                             ('3', '102', 'experiment-state')]:
                    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', (REPO, ident, run_id, name, 'pending', None, None, None))
                    db.execute('INSERT INTO run_observations VALUES(?,?,?,?)', (REPO, run_id, '2099', json.dumps({
                        'head_branch': 'main', 'head_repository': {'full_name': REPO},
                        'path': '.github/workflows/market_research_worker.yaml'})))
                report = execute(root, db, object(), REPO, TrafficBudget(root, 1000000), maximum=0)
                states = dict(db.execute('SELECT artifact_id,status FROM artifacts'))
                self.assertEqual(states, {'1': 'checkpoint_remote_only', '2': 'pending', '3': 'remote_only'})
                self.assertEqual(report['failed'], 0)

    def test_publisher_preserves_hidden_files_and_does_not_embed_reasoning_in_summary(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / '.lock.recovery').write_bytes(b'0')
            (source / 'state.json').write_text(json.dumps({'tasks': {'123': {'status': 'completed', 'forecast': {
                'arms': {'original': {'eventual_probability_yes': .6, 'conditional_reread': 'large reasoning'}}}}}}))
            export([('source', source, None)], root / 'out', 'market-worker', run_id='100', repo=REPO)
            catalog = json.loads((root / 'out/sync-manifest.json').read_text())
            self.assertIn('.lock.recovery', {i['path'] for i in catalog['sources'][0]['files']})
            summary = (root / 'out/summary.json').read_text()
            self.assertIn('0.6', summary)
            self.assertNotIn('large reasoning', summary)
            self.assertTrue((source / '.lock.recovery').is_file())

    def test_publisher_rejects_output_inside_source_and_invalid_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                export([('source', root, None)], root / 'out', 'market-worker', run_id='100', repo=REPO)
            with self.assertRaises(ValueError):
                export([('source', root, None)], root.parent / 'out', 'market-worker', run_id='wrong', repo=REPO)

    def test_all_known_members_need_no_source_zip_requests(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            root = temp / 'local'
            files = {'a.json': b'{"known":true}', 'body.bin': os.urandom(100000)}
            client = Client(zip_bytes(temp, 'source', files))
            for data in files.values():
                local_sync.blob(root, data)
            with local_sync.connect(root) as db:
                report = import_members(root, db, client, REPO, client.metadata['1'], TrafficBudget(root, 1), expected(files))
            self.assertFalse(report['source_zip_opened'])
            self.assertEqual(client.requests, [])
            self.assertEqual(report['reused_files'], 2)

    def test_selective_import_does_not_downgrade_original_zip_archive(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            root = temp / 'local'
            files = {'a.json': b'{}'}
            archive = temp / 'original.zip'
            archive.write_bytes(zip_bytes(temp, 'source', files))
            local_sync.import_zip(root, archive, artifact_id='1', run_id='100')
            client = Client(archive.read_bytes())
            with local_sync.connect(root) as db:
                before = db.execute('SELECT status,archive_sha256 FROM artifacts').fetchone()
                import_members(root, db, client, REPO, client.metadata['1'], TrafficBudget(root, 1), expected(files))
                self.assertEqual(db.execute('SELECT status,archive_sha256 FROM artifacts').fetchone(), before)

    def test_invalid_and_negative_limits_are_rejected(self):
        with TemporaryDirectory() as directory:
            for value in (-1, True, 1.5):
                with self.assertRaises(ValueError):
                    TrafficBudget(directory, value)
