"""Exercise recovery ownership and durable lifetime/daily reservations."""
import json
import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.runtime.budget import reserve_update


class RecoveryBudgetTests(TestCase):
    def test_dead_owner_is_recovered_but_live_owner_is_refused(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            marker = path / '.running.lock'
            marker.write_text(json.dumps({'host': socket.gethostname(), 'pid': 1234}))
            with patch('ForecastAgent.runtime.task_lock.process_alive', return_value=True):
                with self.assertRaises(RuntimeError):
                    with task_lock(path):
                        pass
            with patch('ForecastAgent.runtime.task_lock.process_alive', return_value=False):
                with task_lock(path):
                    self.assertEqual(json.loads(marker.read_text())['pid'], os.getpid())
            self.assertFalse(marker.exists())
            self.assertEqual(len(list(path.glob('.abandoned-lock-*'))), 1)

    def test_verified_restored_run_can_recover_foreign_owner(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / '.running.lock').write_text(json.dumps({'host': 'old-runner', 'github_run_id': '17'}))
            with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'FORECAST_COMPLETED_RESTORE_RUNS': '17',
                                         'FORECAST_RESTORED_STATE_ROOT': directory}):
                with task_lock(path):
                    pass

    def test_daily_and_lifetime_caps_include_reserved_and_failed_calls(self):
        bundle = {'update_attempts': []}
        saved = []
        with patch('ForecastAgent.runtime.budget.update_day', return_value='2026-09-30'):
            for state in ('reserved', 'failed', 'ok'):
                reserve_update(bundle, {'status': state}, lambda: saved.append(len(bundle['update_attempts'])))
            with self.assertRaises(ValueError):
                reserve_update(bundle, {}, lambda: None)
        self.assertEqual(saved, [1, 2, 3])
        bundle['update_attempts'] += [{'budget_day': '2026-09-29'}] * 21
        with patch('ForecastAgent.runtime.budget.update_day', return_value='2026-10-01'):
            with self.assertRaises(ValueError):
                reserve_update(bundle, {}, lambda: None)
