"""The scheduler process owns synchronization and receives its actual outcome."""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ForecastAgent.integrations.local_sync_runner import run


class RunnerTests(unittest.TestCase):
    def test_github_children_remain_windowless_from_pythonw(self):
        from ForecastAgent.local_sync import GitHub
        from ForecastAgent.local_transfer import SignedRanges
        for platform,flags in [('nt',0x08000000),('posix',0)]:
            with patch('ForecastAgent.local_sync.os.name',platform), \
                 patch.object(subprocess,'CREATE_NO_WINDOW',0x08000000,create=True), \
                 patch('ForecastAgent.local_sync.subprocess.run',
                       return_value=SimpleNamespace(returncode=0,stdout=b'{}')) as child:
                client=GitHub('gh');client.api('repos/example/repo')
                self.assertEqual([call.kwargs['creationflags'] for call in child.call_args_list],[flags,flags])
                reader=SignedRanges('gh','example/repo',1,{'size_in_bytes':1},None)
                with patch('ForecastAgent.local_transfer.subprocess.check_output',side_effect=RuntimeError('fixture stop')) as token:
                    with self.assertRaisesRegex(RuntimeError,'fixture stop'):reader.resolve()
                    self.assertEqual(token.call_args.kwargs['creationflags'],flags)

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
