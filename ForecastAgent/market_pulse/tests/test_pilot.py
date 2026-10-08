"""Offline tests for pilot ledger isolation and platform credential boundaries."""
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ForecastAgent.competition.queue import load, save
from ForecastAgent.market_pulse.pilot import child, selected_ids, prepare


class PilotTests(unittest.TestCase):
    def test_bounded_distinct_batch_selection(self):
        self.assertEqual(selected_ids(['46176', '46195', '46193', '46181', '46198']),
                         ('46176', '46195', '46193', '46181', '46198'))
        for invalid in ([], ['1'] * 2, ['1', '2', '3', '4', '5', '6'], ['invalid'], ['1/../2']):
            with self.assertRaisesRegex(ValueError, 'one to five'):
                selected_ids(invalid)

    def test_existing_selection_cannot_change_or_launch_new_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / 'manifest.json', {'rows': [{'id': '46176'}]})
            with patch('ForecastAgent.market_pulse.pilot.snapshot') as capture:
                with self.assertRaisesRegex(ValueError, 'selection changed'):
                    prepare(root, ids=['46195'])
                capture.assert_not_called()

    def test_runner_logs_are_outside_collector_ledger(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory) / 'task'
            folder.mkdir()
            (folder / 'stdout.log').write_text('Runner log', encoding='utf-8')
            source = Path(directory) / 'input.json'
            save(source, {'id': '123'})
            calls = []

            def fake_collect(request, retrieval):
                calls.append(retrieval)
                self.assertEqual(retrieval, folder / 'retrieval')
                self.assertFalse(retrieval.exists())
                native = retrieval / 'release-1.0.5'
                save(native / 'collection/bundle.json', {'result': {}, 'model_attempts': []})
                save(native / 'state.json', {'stage': 'collection'})
                return {'result': {'incomplete': True}}

            with patch.dict(os.environ, {'METACULUS_TOKEN': '', 'FORECAST_PROVIDER_FAILOVER_MODULE': ''}), \
                    patch('ForecastAgent.releases.v1_0_5.collect', side_effect=fake_collect):
                child(source, folder)
            self.assertEqual(len(calls), 1)
            result = load(folder / 'collection-result.json')
            self.assertFalse(result['submitted'])
            self.assertFalse(result['analysis_run'])

    def test_platform_token_rejected_before_collector(self):
        with patch.dict(os.environ, {'METACULUS_TOKEN': 'placeholder-for-test'}):
            with self.assertRaisesRegex(ValueError, 'must not reach'):
                child(Path('unused'), Path('unused'))

    def test_transport_installed_before_release_aliases(self):
        with TemporaryDirectory() as directory:
            module = Path(directory) / 'transport.py'
            module.write_text('import urllib.request\n'
                'def install():\n'
                '    def guarded(*args, **kwargs):\n'
                '        raise RuntimeError("No network permitted in this test")\n'
                '    guarded.preimport_test = True\n'
                '    urllib.request.urlopen = guarded\n', encoding='utf-8')
            env = dict(os.environ, FORECAST_PROVIDER_FAILOVER_MODULE=str(module))
            code = ('import ForecastAgent.market_pulse.pilot; '
                    'from ForecastAgent.providers.ultra import urlopen; '
                    'from ForecastAgent.providers.tavily_search import urlopen as search_open; '
                    'assert getattr(urlopen,"preimport_test",False); '
                    'assert getattr(search_open,"preimport_test",False)')
            result = subprocess.run([sys.executable, '-c', code], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
