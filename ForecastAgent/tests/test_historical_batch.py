import json
import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from ForecastAgent.historical_batch import initialize, run_batch, archive
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.telemetry import model_observer
from ForecastAgent.providers.ultra import ask_ultra
from ForecastAgent.evidence.temporal import temporal_report


def row(ident):
    return {'id': str(ident), 'question': 'Will the event occur?',
            'resolution_criteria': 'Official announcement before the deadline.',
            'as_of_utc': '2026-01-01T00:00:00Z'}


class HistoricalBatchTests(TestCase):
    def test_labels_and_changed_inputs_refused(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            source = Path(temp) / 'input.json'
            source.write_text(json.dumps([row(1)]))
            initialize(root, source)
            source.write_text(json.dumps([{**row(1), 'resolved_to': 1}]))
            with self.assertRaises(ValueError):
                initialize(root, source)
            source.write_text(json.dumps([row(2)]))
            with self.assertRaises(ValueError):
                initialize(root, source)

    def test_failure_isolated_completed_cached_and_missing_state_blocks(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            source = Path(temp) / 'input.json'
            source.write_text(json.dumps([row(1), row(2), row(3)]))
            initialize(root, source)
            def runner(request, directory, *_):
                if request['id'] == '1':
                    raise RuntimeError('secret failure')
                task = RetrievalTask(directory, request)
                task.bundle['result'] = {'status': 'collected_with_gaps'}
                task.save()
                return task.bundle
            batch = run_batch(root, 'secret', '', limit=2, runner=runner)
            self.assertEqual(batch['tasks']['1']['status'], 'failed')
            self.assertNotIn('secret', batch['tasks']['1']['attempts'][0]['detail'])
            self.assertEqual(batch['tasks']['2']['status'], 'complete')
            (root / 'tasks' / '1' / 'bundle.json').unlink()
            batch = run_batch(root, '', '', runner=runner)
            self.assertEqual(batch['tasks']['1']['status'], 'missing_task_state')
            self.assertEqual(len(batch['tasks']['2']['attempts']), 1)
            self.assertEqual(batch['tasks']['3']['status'], 'complete')
            result = archive(root, Path(temp) / 'export.zip')
            with ZipFile(result['path']) as saved:
                manifest = json.loads(saved.read('archive_manifest.json'))
                self.assertIn('tasks/2/intelligence.json', manifest)
                for path, expected in manifest.items():
                    self.assertEqual(hashlib.sha256(saved.read(path)).hexdigest(), expected)
            with self.assertRaises(ValueError):
                archive(root, root / 'bad.zip')

    @patch('ForecastAgent.providers.ultra.time.sleep')
    @patch('ForecastAgent.providers.ultra.urlopen')
    def test_model_retries_preserve_full_payload_usage_and_redact(self, opener, sleep):
        opener.side_effect = [BytesIO(b'{"error":{"message":"temporary"}}'),
            BytesIO(b'{"id":"response-1","usage":{"total_tokens":42},"choices":[{"finish_reason":"stop","message":{"content":"secret"}}]}')]
        with TemporaryDirectory() as temp:
            task = RetrievalTask(Path(temp), {**row(1), 'pipeline': 'collection'})
            observer = model_observer(task, ('secret',))
            ask_ultra([], 'secret', tools=[], observer=observer)
            attempts = task.bundle['model_attempts']
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0]['status'], 'missing_choices')
            self.assertEqual(attempts[1]['usage']['total_tokens'], 42)
            saved = (Path(temp) / attempts[1]['path']).read_text()
            self.assertNotIn('secret', saved)
            self.assertIn('response_body', saved)
            task.bundle['model_attempts'] = [{}] * 72
            with self.assertRaises(RuntimeError):
                ask_ultra([], 'secret', tools=[], observer=observer)
            self.assertEqual(opener.call_count, 2)

    def test_reservation_survives_without_response(self):
        with TemporaryDirectory() as temp:
            request = {**row(1), 'pipeline': 'collection'}
            task = RetrievalTask(Path(temp), request)
            model_observer(task)('reserve', {'started_at_utc': 'now', 'retry_index': 0,
                                           'status': 'reserved', 'request': {}})
            restored = RetrievalTask(Path(temp), request)
            self.assertEqual(restored.bundle['model_attempts'][0]['status'], 'reserved')

    def test_batch_resumes_consumed_search_reservation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            source = Path(temp) / 'input.json'
            source.write_text(json.dumps([row(1)]))
            initialize(root, source)
            def interrupted(request, directory, *_):
                task = RetrievalTask(directory, request)
                task.bundle['searches'].append({'status': 'reserved'})
                task.save()
                raise RuntimeError('Interrupted')
            run_batch(root, '', '', runner=interrupted)
            batch = json.loads((root / 'batch.json').read_text())
            batch['tasks']['1']['retry_after_utc'] = '2000-01-01T00:00:00Z'
            (root / 'batch.json').write_text(json.dumps(batch))
            def resumed(request, directory, *_):
                task = RetrievalTask(directory, request)
                self.assertEqual(len(task.bundle['searches']), 1)
                self.assertEqual(task.bundle['searches'][0]['status'], 'reserved')
                task.bundle['result'] = {'status': 'collected_with_gaps'}
                task.save()
                return task.bundle
            batch = run_batch(root, '', '', runner=resumed)
            self.assertEqual(len(batch['tasks']['1']['attempts']), 2)
            self.assertEqual(batch['tasks']['1']['status'], 'complete')

    def test_time_categories_do_not_promote_old_publication(self):
        bundle = {'request': row(1), 'pages': {
            'old': {'published_at': '2025-12-01T00:00:00Z', 'retrieved_at_utc': '2026-09-30T00:00:00Z'},
            'unknown': {'retrieved_at_utc': '2026-09-30T00:00:00Z'},
            'snapshot': {'retrieved_at_utc': '2025-12-01T00:00:00Z', 'temporal_status': 'local_pre_cutoff_capture'}},
            'quarantine': [{'hit': {'url': 'late', 'published_date': '2026-04-01T00:00:00Z'}}]}
        report = temporal_report(bundle)
        self.assertEqual([r['category'] for r in report['records']],
            ['current_capture_of_historical_material', 'unknown_publication', 'pre_cutoff_snapshot', 'quarantined'])
        self.assertFalse(report['historical_clean'])
