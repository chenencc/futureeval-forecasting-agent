import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

from ForecastAgent.local_sync import import_zip, connect, status, sync


def make_archive(path, files, manifest=False):
    with ZipFile(path, 'w') as saved:
        for name, content in files.items():
            saved.writestr(name, content)
        if manifest:
            saved.writestr('archive_manifest.json', json.dumps({name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}))


class LocalSyncTests(TestCase):
    def test_analysis_records_are_indexed_separately(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'data'
            archive = Path(temp) / 'analysis.zip'
            make_archive(archive, {'tasks/123/prediction.json': '{"id":"123","probability_yes":0.4}',
                                  'tasks/123/citation-audit.json': '{"status":"degraded_quote_only"}',
                                  'tasks/123/mercury-http/001.json': '{"status":"received"}'})
            import_zip(root, archive, artifact_id='analysis', run_id='100')
            with connect(root) as db:
                records = db.execute('SELECT kind,question_id FROM records WHERE kind LIKE "analysis_%" ORDER BY kind').fetchall()
            self.assertEqual(records, [('analysis_citation_audit', '123'), ('analysis_prediction', '123'), ('analysis_provider_transport', '123')])

    def test_nested_campaign_is_indexed_and_cached(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)/'data'
            inner=Path(temp)/'inner.zip'
            outer=Path(temp)/'outer.zip'
            make_archive(inner,{'tasks/123/bundle.json':json.dumps({'request':{'id':'123','question':'Question'}})},manifest=True)
            with ZipFile(outer,'w') as saved:
                saved.write(inner,'historical-2026-135.zip')
            import_zip(root,outer,artifact_id='1')
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM question_versions').fetchone()[0],1)
            self.assertEqual(import_zip(root,outer,artifact_id='1')['status'],'cached')
            self.assertEqual(status(root)['artifacts'],2)

    def test_versioned_index_dedup_and_model_link(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'data'
            archive = Path(temp) / 'source.zip'
            bundle = {'request': {'id': '123', 'question': 'Question', 'as_of_utc': '2026-01-01T00:00:00Z'},
                      'pages': {'url': {'content': 'Original text'}}, 'searches': [{'status': 'completed'}]}
            make_archive(archive, {'tasks/123/bundle.json': json.dumps(bundle),
                'tasks/123/model_calls/0001.json': '{"usage":{"total_tokens":12}}'}, manifest=True)
            result = import_zip(root, archive, artifact_id='1', run_id='100')
            self.assertEqual(result['status'], 'imported')
            self.assertEqual(import_zip(root, archive, artifact_id='1')['status'], 'cached')
            before = status(root)
            import_zip(root, archive, artifact_id='2', run_id='101')
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT sha256) FROM files').fetchone()[0], before['files'])
                self.assertEqual(db.execute('SELECT question_id FROM records WHERE kind="model_transport" LIMIT 1').fetchone()[0], '123')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM question_versions').fetchone()[0], 2)

    def test_manifest_failure_is_not_partially_committed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'data'
            archive = Path(temp) / 'bad.zip'
            with ZipFile(archive, 'w') as saved:
                saved.writestr('a.json', '{}')
                saved.writestr('archive_manifest.json', json.dumps({'a.json': 'invalid'}))
            with self.assertRaises(ValueError):
                import_zip(root, archive)
            self.assertEqual(status(root)['files'], 0)
            self.assertEqual(status(root)['artifacts'], 0)

    def test_traversal_rejected(self):
        with TemporaryDirectory() as temp:
            archive = Path(temp) / 'bad.zip'
            make_archive(archive, {'../outside.json': '{}'})
            with self.assertRaises(ValueError):
                import_zip(Path(temp) / 'data', archive)

    def test_download_failure_retry_without_reacquisition(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'data'
            class Client:
                failed = True
                downloads = 0
                def api(self, endpoint):
                    if 'artifacts?' in endpoint:
                        return {'artifacts': [{'id': 2, 'name': 'futureeval-monitor-state', 'expired': False}]}
                    if 'page=2' in endpoint:
                        return {'workflow_runs': []}
                    return {'workflow_runs': [{'id': 1, 'name': 'Monitor', 'head_sha': 'commit',
                        'updated_at': '2099-01-01T00:00:00Z', 'created_at': '2099-01-01T00:00:00Z', 'conclusion': 'failure'}]}
                def download(self, repo, ident, target):
                    self.downloads += 1
                    if self.failed:
                        raise RuntimeError('Temporary download failure')
                    make_archive(target, {'state.json': '{"seen_question_ids":[123]}'})
            client = Client()
            self.assertEqual(sync(root, client=client)['failed'], 1)
            client.failed = False
            with connect(root) as db:
                db.execute('UPDATE retries SET next_retry_at="2000-01-01T00:00:00+00:00"')
            self.assertEqual(sync(root, client=client)['imported'], 1)
            self.assertEqual(sync(root, client=client)['imported'], 0)
            self.assertEqual(client.downloads, 2)
            self.assertEqual(status(root)['artifact_states'], {'imported': 1})
