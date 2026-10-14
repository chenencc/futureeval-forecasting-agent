"""Budget holds never become HTTP use, missing files or renewed search allowances."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.common import digest, policy, read, state, write
from ForecastAgent.market_repricing import budgets, worker, deployment

CLOCK = '2026-10-14T09:00:00+00:00'


class BudgetRecovery(unittest.TestCase):
    def records(self, count, key='other', run='previous'):
        return [{'key': key, 'kind': 'model', 'day': CLOCK[:10], 'run_id': run,
                 'reserved_at': CLOCK, 'status': 'failed'} for _ in range(count)]

    def test_daily_preflight_waits_until_utc_midnight_without_execution_or_http(self):
        with tempfile.TemporaryDirectory() as root, patch.object(budgets, 'now', return_value=CLOCK):
            ledger = {'tasks': {'t': {'executions': 0, 'status': 'queued'}},
                      'provider_reservations': self.records(900)}
            original = copy.deepcopy(ledger['provider_reservations'])
            with patch.object(worker, 'acquire') as collector:
                worker.execute(root, ledger, 't', policy(), None, collector=collector)
            collector.assert_not_called()
            task = ledger['tasks']['t']
            self.assertEqual(task['status'], 'budget_wait')
            self.assertEqual(task['retry_at'], '2026-10-15T00:00:00+00:00')
            self.assertEqual(task['executions'], 0)
            self.assertEqual(ledger['provider_reservations'], original)
            with patch.object(budgets, 'now', return_value='2026-10-15T00:00:01+00:00'):
                self.assertIsNone(budgets.availability(ledger, 't', policy()))

    def test_task_lifetime_cap_remains_terminal_and_dispatch_wait_is_transient(self):
        ledger = {'provider_reservations': self.records(60, key='t')}
        with patch.object(budgets, 'now', return_value=CLOCK):
            self.assertEqual(budgets.availability(ledger, 't', policy())['scope'], 'task_lifetime')
            ledger['provider_reservations'] = self.records(200, run='fixture')
            with patch.dict('os.environ', {'GITHUB_RUN_ID': 'fixture'}):
                self.assertEqual(budgets.availability(ledger, 't', policy())['scope'], 'dispatch')
            with patch.dict('os.environ', {'GITHUB_RUN_ID': 'next'}):
                self.assertIsNone(budgets.availability(ledger, 't', policy()))

    def test_wrapped_native_denial_does_not_consume_service_execution(self):
        import urllib.request
        with tempfile.TemporaryDirectory() as root:
            ledger = {'tasks': {'t': {'executions': 1, 'status': 'retry_wait'}}, 'provider_reservations': []}
            def collect(task, folder):
                ledger['provider_reservations'].extend(self.records(900))
                try:
                    urllib.request.urlopen('https://openrouter.ai/api/v1/chat/completions')
                except budgets.ProviderCap:
                    raise FileNotFoundError('Native collector swallowed the budget hold')
            with patch.object(budgets, 'now', return_value=CLOCK), patch('urllib.request.urlopen') as http:
                worker.execute(root, ledger, 't', policy(), None, collector=collect)
            http.assert_not_called()
            self.assertEqual(ledger['tasks']['t']['status'], 'budget_wait')
            self.assertEqual(ledger['tasks']['t']['executions'], 2)
            self.assertEqual(budgets.executions_used(ledger['tasks']['t']), 1)
            self.assertFalse((Path(root) / 'tasks' / digest('t') / 'failure.json').exists())

    def test_missing_package_has_explicit_incomplete_status(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(budgets.CollectionIncomplete):
                budgets.require_package(Path(root), {'status': 'collection_incomplete'}, [])

    def denial(self, folder):
        native = folder / 'development-intelligence/retrieval/development-channels/collection'
        path = native / 'model_calls/1.json'
        write(path, {'status': 'transport_error', 'error': 'ProviderCap', 'started_at_utc': CLOCK})
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        write(native / 'bundle.json', {'model_attempts': [{'path': 'model_calls/1.json', 'sha256': sha,
                                                        'status': 'transport_error'}]})
        return path

    def test_legacy_recovery_is_hash_checked_one_time_and_preserves_every_original(self):
        with tempfile.TemporaryDirectory() as root:
            task = {'status': 'retry_wait', 'executions': 2, 'last_attempt_at': CLOCK}
            ledger = {'tasks': {'t': task}, 'provider_reservations': self.records(7)}
            folder = Path(root) / 'tasks' / digest('t')
            path = self.denial(folder)
            write(folder / 'failure.json', {'error_type': 'FileNotFoundError'})
            originals = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            records = copy.deepcopy(ledger['provider_reservations'])
            self.assertEqual(budgets.recover_legacy(root, ledger), ['t'])
            self.assertEqual(budgets.recover_legacy(root, ledger), [])
            self.assertEqual(task['executions'], 2)
            self.assertEqual(budgets.executions_used(task), 1)
            self.assertEqual(ledger['provider_reservations'], records)
            for p, raw in originals.items(): self.assertEqual(p.read_bytes(), raw)
            write(path, {'status': 'received'})
            with self.assertRaises(ValueError): budgets.native_denials(folder)

    def test_physical_attempt_during_failed_execution_prevents_refund(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = {'tasks': {'t': {'status': 'retry_wait', 'executions': 1, 'last_attempt_at': CLOCK}},
                      'provider_reservations': self.records(1, key='t')}
            folder = Path(root) / 'tasks' / digest('t')
            self.denial(folder); write(folder / 'failure.json', {'error_type': 'FileNotFoundError'})
            self.assertEqual(budgets.recover_legacy(root, ledger), [])

    def test_exact_400_to_900_migration_preserves_all_counters_and_other_changes_fail(self):
        cfg = policy(); old = cfg | {'maximum_worker_model_attempts_per_utc_day': 400}
        with tempfile.TemporaryDirectory() as root:
            ledger = state(root, 'worker', old)
            ledger.update(tasks={'done': {'status': 'completed', 'executions': 3}},
                          provider_reservations=self.records(400), daily={'old': 50})
            write(Path(root) / 'state.json', ledger)
            migrated = state(root, 'worker', cfg)
            for field in ('tasks', 'provider_reservations', 'daily'):
                self.assertEqual(migrated[field], ledger[field])
            self.assertEqual(migrated['identity']['policy_sha256'], digest(cfg))
            with self.assertRaises(ValueError): state(root, 'worker', cfg | {'maximum_worker_model_attempts_per_utc_day': 901})

    def test_budget_promotion_requires_exact_unchanged_stages(self):
        with tempfile.TemporaryDirectory() as root:
            write(Path(root) / 'state.json', {'tasks': {'old': {'status': 'budget_wait',
                  'pipeline_commit': deployment.BUDGET_PREDECESSOR}}})
            raw = (Path(root) / 'state.json').read_bytes()
            result = deployment.validate_pending(root, Path(root) / 'monitor', {'pipeline_commit': '1' * 40})
            self.assertEqual(result['pending_checked'], 1)
            self.assertEqual((Path(root) / 'state.json').read_bytes(), raw)
            with patch.object(deployment, 'budget_compatibility', side_effect=ValueError('changed')):
                with self.assertRaises(ValueError): deployment.validate_pending(root, Path(root) / 'monitor', {'pipeline_commit': '1' * 40})


if __name__ == '__main__': unittest.main()
