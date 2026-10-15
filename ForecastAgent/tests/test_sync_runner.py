"""The scheduler process owns synchronization and receives its actual outcome."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ForecastAgent.integrations.local_sync_runner import run


class RunnerTests(unittest.TestCase):
    def test_complete_native_failure_is_logged_in_process(self):
        with TemporaryDirectory() as folder, \
             patch('ForecastAgent.integrations.local_sync_runner.os.chdir'), \
             patch('ForecastAgent.integrations.local_sync_runner.importlib.import_module',
                   return_value=SimpleNamespace(main=lambda:(_ for _ in ()).throw(RuntimeError('native_failure')))):
            self.assertEqual(run('databases',folder,'gh'),1)
            log=(Path(folder)/'sync.log').read_text()
            self.assertIn('RuntimeError: native_failure',log)
            self.assertIn('sync exit 1',log)

    def test_deferred_exit_is_preserved_without_launching_child(self):
        with TemporaryDirectory() as folder, \
             patch('ForecastAgent.integrations.local_sync_runner.os.chdir'), \
             patch('ForecastAgent.integrations.local_sync_runner.importlib.import_module',
                   return_value=SimpleNamespace(main=lambda:(_ for _ in ()).throw(SystemExit(3)))):
            self.assertEqual(run('archive',folder,'gh'),3)
            self.assertIn('sync exit 3',(Path(folder)/'logs/sync.log').read_text())


if __name__=='__main__':unittest.main()
