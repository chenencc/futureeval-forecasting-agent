"""Traffic, provenance and interruption tests without providers or network."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch,MagicMock
from zipfile import ZipFile, ZIP_DEFLATED

from ForecastAgent import local_sync
from ForecastAgent.incremental_sync import import_members, import_catalog, validate_expected, execute,fresh_pack_order
from ForecastAgent.infrastructure.local_sync_export import export, PREFIX
from ForecastAgent.local_transfer import ArtifactZip, RangeFile, TrafficBudget, TransferDeferred,SignedRanges,CachedBlocks

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
    def test_many_small_members_share_one_verified_block_and_survive_restart(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            files={f'tasks/{i}/receipt.json':json.dumps({'id':i}).encode() for i in range(200)}
            raw=zip_bytes(root,'many',files)
            info={'id':1,'size_in_bytes':len(raw),'digest':hashlib.sha256(raw).hexdigest()}
            calls=[]
            def fetch(start,stop):
                calls.append((start,stop));return raw[start:stop]
            for _ in range(2):
                blocks=CachedBlocks(root/'blocks',info,fetch)
                with ArtifactZip(RangeFile(root/'ranges',info,blocks)) as archive:
                    for name,data in files.items():self.assertEqual(archive.read(name),data)
            self.assertEqual(calls,[(0,len(raw))])

    def test_old_signed_redirect_refreshes_before_payload_without_hidden_retry(self):
        with TemporaryDirectory() as directory:
            budget=TrafficBudget(directory,100)
            reader=SignedRanges('gh',REPO,1,{'size_in_bytes':10},budget)
            reader.url='https://example.blob.core.windows.net/saved'
            reader.refresh_at=10
            response=MagicMock()
            response.status=206
            response.headers={'Content-Range':'bytes 0-0/10'}
            response.read.return_value=b'x'
            opener=MagicMock()
            opener.open.return_value.__enter__.return_value=response
            with patch('ForecastAgent.local_transfer.time.monotonic',return_value=11), \
                 patch.object(reader,'resolve') as resolve, \
                 patch('ForecastAgent.local_transfer.build_opener',return_value=opener):
                self.assertEqual(reader(0,1),b'x')
            resolve.assert_called_once()
            opener.open.assert_called_once()
            self.assertEqual(budget.downloaded,1)
            request=opener.open.call_args.args[0]
            self.assertNotIn('Authorization',request.headers)

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
    def test_fresh_case_pack_precedes_cold_historical_packs_without_omitting_files(self):
        summary={'sources':{'state':{'state.json':{'tasks':[
            {'id':'old','forecast':{'completed_at':'2026-10-14T00:00:00Z'}},
            {'id':'new','forecast':{'completed_at':'2026-10-15T00:00:00Z'}}]}}}}
        ordered=fresh_pack_order(summary,{'artifact_name':'state'})
        newest='packs/'+hashlib.sha256(b'"new"').hexdigest()+'.tar.xz'
        self.assertEqual(ordered[0],newest)
        with TemporaryDirectory() as directory:
            temp=Path(directory);root=temp/'local'
            files={'packs/000-old.tar.xz':b'old',newest:b'new','market-http/a.json':b'{}'}
            client=Client(zip_bytes(temp,'fresh',files))
            saved=[];original=local_sync.blob
            def record(root,data):saved.append(data);return original(root,data)
            with local_sync.connect(root) as db,patch('ForecastAgent.local_sync.blob',side_effect=record):
                import_members(root,db,client,REPO,client.metadata['1'],TrafficBudget(root,0),expected(files),ordered)
            self.assertEqual(saved,[b'new',b'old',b'{}'])

    def test_body_packs_are_persisted_before_http_cache_inside_worker(self):
        with TemporaryDirectory() as directory:
            temp=Path(directory);root=temp/'local'
            files={'market-http/a.json':b'{"status":409}','packs/a.tar.xz':b'saved_body_pack'}
            client=Client(zip_bytes(temp,'worker',files))
            saved=[];original=local_sync.blob
            def record(root,data):saved.append(data);return original(root,data)
            with local_sync.connect(root) as db,patch('ForecastAgent.local_sync.blob',side_effect=record):
                import_members(root,db,client,REPO,client.metadata['1'],TrafficBudget(root,0),expected(files))
            self.assertEqual(saved,[b'saved_body_pack',b'{"status":409}'])

    def test_worker_bodies_are_not_starved_by_newer_monitor_cache(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            items={}
            with local_sync.connect(root) as db:
                for ident,run,stream,workflow in [('1','100','market-worker','market_research_worker.yaml'),
                                                  ('2','200','market-monitor','market_research_monitor.yaml'),
                                                  ('0','90','market-worker','market_research_worker.yaml'),
                                                  ('3','95','official-worker','official_competition.yaml')]:
                    name=PREFIX+stream
                    items[ident]={'id':int(ident),'name':name,'workflow_run':{'id':int(run)}}
                    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',(REPO,ident,run,name,'pending',None,None,None))
                    db.execute('INSERT INTO run_observations VALUES(?,?,?,?)',(REPO,run,'2099',json.dumps({
                        'head_branch':'main','head_repository':{'full_name':REPO},'path':'.github/workflows/'+workflow})))
                client=MagicMock();client.api.side_effect=lambda endpoint:items[endpoint.rsplit('/',1)[-1]]
                with patch('ForecastAgent.incremental_sync.import_catalog',return_value={}) as imported:
                    execute(root,db,client,REPO,TrafficBudget(root,0),maximum=2)
                self.assertEqual([str(call.args[4]['id']) for call in imported.call_args_list],['1','3'])

    def test_batch_preserves_verified_files_and_rejects_the_bad_member(self):
        with TemporaryDirectory() as directory:
            temp=Path(directory);root=temp/'local'
            files={f'{i:03}.json':json.dumps({'i':i}).encode() for i in range(80)}
            client=Client(zip_bytes(temp,'batch',files))
            declared=expected(files);declared[70]['sha256']='0'*64
            with local_sync.connect(root) as db:
                with self.assertRaisesRegex(ValueError,'hash mismatch'):
                    import_members(root,db,client,REPO,client.metadata['1'],TrafficBudget(root,0),declared)
            with local_sync.connect(root) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM files').fetchone()[0],70)
                self.assertEqual(db.execute('SELECT count(*) FROM artifacts').fetchone()[0],0)
                result=import_members(root,db,client,REPO,client.metadata['1'],TrafficBudget(root,0),expected(files))
                self.assertEqual(db.execute('SELECT count(*) FROM files').fetchone()[0],80)
                self.assertEqual(result['expected_files'],80)

    def test_partial_sync_returns_nonzero_for_scheduled_task_health(self):
        with patch('sys.argv',['sync','sync']), \
             patch('ForecastAgent.local_sync.sync',return_value={'status':'partial','failed':1}), \
             patch('ForecastAgent.local_sync.publish_status'),patch('builtins.print'):
            with self.assertRaises(SystemExit) as exited:local_sync.main()
        self.assertEqual(exited.exception.code,1)

    def test_large_valid_summary_imports_and_wrong_identity_still_fails(self):
        with TemporaryDirectory() as directory:
            temp=Path(directory)
            source=temp/'source';source.mkdir()
            (source/'state.json').write_text('{"tasks":{}}')
            export([('market-repricing-v2-worker-state',source,None)],temp/'catalog',
                   'market-worker',run_id='100',repo=REPO)
            summary_path=temp/'catalog/summary.json'
            summary=json.loads(summary_path.read_bytes())
            summary['test_padding']='x'*(2*1024**2+1)
            summary_path.write_text(json.dumps(summary))
            files={p.relative_to(temp/'catalog').as_posix():p.read_bytes()
                   for p in (temp/'catalog').rglob('*') if p.is_file()}
            client=Client(zip_bytes(temp,'catalog',files),name=PREFIX+'market-worker')
            root=temp/'local'
            with local_sync.connect(root) as db:
                # A later source error proves the complete >2MiB summary passed
                # its bound; catalog provenance must still reject a changed run.
                with self.assertRaisesRegex(RuntimeError,'source is missing'):
                    import_catalog(root,db,client,REPO,client.metadata['1'],TrafficBudget(root,10000000))
                self.assertTrue((root/'sync-catalogs/market-worker/latest-summary.json').is_file())
                client.metadata['1']['workflow_run']['id']=101
                with self.assertRaisesRegex(ValueError,'provenance'):
                    import_catalog(root,db,client,REPO,client.metadata['1'],TrafficBudget(root,10000000))

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
            catalog_data = zip_bytes(temp, 'catalog', {p.relative_to(temp / 'catalog').as_posix(): p.read_bytes()
                for p in (temp / 'catalog').rglob('*') if p.is_file()})
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

    def test_offline_research_view_preserves_bytes_without_download(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            root = temp / 'local'
            data = {'tasks/123/analysis.json': b'{"status":"saved"}', 'raw.bin': b'original'}
            archive = temp / 'original.zip'
            archive.write_bytes(zip_bytes(temp, 'source', data))
            local_sync.import_zip(root, archive, artifact_id='1', run_id='100')
            report = local_sync.materialize(root, '1', temp / 'view')
            self.assertEqual(report['network_requests'], 0)
            for name, expected_bytes in data.items():
                self.assertEqual((temp / 'view' / name).read_bytes(), expected_bytes)
            (temp / 'view/raw.bin').write_bytes(b'modified')
            with self.assertRaises(ValueError):
                local_sync.materialize(root, '1', temp / 'view')

    def test_partial_or_protected_view_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with local_sync.connect(root) as db:
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', (REPO, '1', '100', 'partial', 'transfer_wait', None, None, None))
            with self.assertRaises(ValueError):
                local_sync.materialize(root, '1', root / 'view')
            with self.assertRaises(ValueError):
                local_sync.materialize(root, '1', root / 'blobs/view')

    def test_rotated_source_is_complete_only_when_all_original_bytes_exist(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / 'source'
            source.mkdir()
            files = {'state.json': b'{"tasks":{}}', 'body.bin': b'old immutable original'}
            for name, data in files.items():
                (source / name).write_bytes(data)
            export([('market-repricing-v2-worker-state', source, None)], temp / 'catalog', 'market-worker',
                   run_id='100', repo=REPO, source_ids={'market-repricing-v2-worker-state': '1'})
            catalog_bytes = zip_bytes(temp, 'catalog-source', {p.relative_to(temp / 'catalog').as_posix(): p.read_bytes()
                for p in (temp / 'catalog').rglob('*') if p.is_file()})
            client = Client(catalog_bytes, name=PREFIX + 'market-worker', ident=2)
            root = temp / 'complete'
            local_sync.blob(root, files['body.bin'])
            with local_sync.connect(root) as db:
                report = import_catalog(root, db, client, REPO, client.metadata['2'], TrafficBudget(root, 100000))
                self.assertFalse(report['catalog_sources'][0]['source_zip_opened'])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM files WHERE artifact_id="1"').fetchone()[0], 2)
            incomplete = temp / 'incomplete'
            with local_sync.connect(incomplete) as db:
                with self.assertRaises(RuntimeError):
                    import_catalog(incomplete, db, client, REPO, client.metadata['2'], TrafficBudget(incomplete, 100000))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM artifacts WHERE status="files_imported"').fetchone()[0], 0)

    def test_declared_source_id_mismatch_is_rejected(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / 'source'
            source.mkdir()
            (source / 'state.json').write_bytes(b'{}')
            export([('market-repricing-v2-worker-state', source, None)], temp / 'catalog', 'market-worker',
                   run_id='100', repo=REPO, source_ids={'market-repricing-v2-worker-state': '9'})
            client = Client(zip_bytes(temp, 'source', {'state.json': b'{}'}))
            data = zip_bytes(temp, 'catalog-source', {p.relative_to(temp / 'catalog').as_posix(): p.read_bytes()
                for p in (temp / 'catalog').rglob('*') if p.is_file()})
            client.data['2'] = data
            client.metadata['2'] = {'id': 2, 'name': PREFIX + 'market-worker', 'size_in_bytes': len(data),
                'digest': 'sha256:catalog', 'expired': False, 'workflow_run': {'id': 100}}
            with local_sync.connect(temp / 'local') as db:
                with self.assertRaises(ValueError):
                    import_catalog(temp / 'local', db, client, REPO, client.metadata['2'], TrafficBudget(temp / 'local', 100000))

    def test_superseded_health_is_retired_before_future_retry_deadline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with local_sync.connect(root) as db:
                for ident, run_id, status in [('1', '100', 'failed'), ('2', '101', 'imported')]:
                    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', (REPO, ident, run_id, 'futureeval-monitor-health', status, None, None, None))
                    db.execute('INSERT INTO run_observations VALUES(?,?,?,?)', (REPO, run_id, '2099', json.dumps({
                        'head_branch': 'main', 'head_repository': {'full_name': REPO},
                        'path': '.github/workflows/run_bot_on_tournament.yaml'})))
                db.execute('INSERT INTO retries VALUES(?,?,?,?)', (REPO, '1', 3, '2099-01-01'))
                report = execute(root, db, object(), REPO, TrafficBudget(root, 1), maximum=0)
                self.assertEqual(db.execute('SELECT status FROM artifacts WHERE artifact_id="1"').fetchone()[0], 'checkpoint_remote_only')
                self.assertEqual(report['failed'], 0)

    def test_deleted_artifact_is_a_gap_when_run_listing_proves_absence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            class Gone:
                def api(self, endpoint):
                    if '/actions/artifacts/' in endpoint:
                        raise local_sync.GitHubReadError(404)
                    return {'artifacts': []}
            with local_sync.connect(root) as db:
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', (REPO, '1', '100', 'futureeval-questions-100', 'pending', None, None, None))
                db.execute('INSERT INTO run_observations VALUES(?,?,?,?)', (REPO, '100', '2099', json.dumps({
                    'head_branch': 'main', 'head_repository': {'full_name': REPO},
                    'path': '.github/workflows/run_bot_on_tournament.yaml'})))
                report = execute(root, db, Gone(), REPO, TrafficBudget(root, 1))
                self.assertEqual(db.execute('SELECT status FROM artifacts').fetchone()[0], 'expired')
                self.assertEqual(report['failed'], 0)
