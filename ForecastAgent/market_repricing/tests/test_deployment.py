"""Immutable market pins, resume compatibility and independent release controls."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing import deployment
from ForecastAgent.market_repricing.common import digest, read, write
from ForecastAgent.market_repricing.evidence_capture import policy_identity
from ForecastAgent.market_repricing.worker import quarantine_invalid_results

SHA = '1' * 40
OTHER = '2' * 40


class DeploymentTests(unittest.TestCase):
    def test_only_full_sha_can_pin_market_code(self):
        for invalid in ('', 'main', 'market-v0.2.1', '1' * 7):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                deployment.verify_checkout('.', invalid)
        with patch.dict(os.environ, {'MARKET_PIPELINE_SHA': SHA}):
            release = deployment.task_release()
            self.assertEqual(release['pipeline_version'], deployment.VERSION)
            self.assertEqual(release['pipeline_commit'], SHA)
            self.assertEqual(release['analysis_arms'], ['original', 'mapped', 'official_map'])

    def test_checkout_mismatch_and_dirty_tree_fail_before_requests(self):
        root = Path(deployment.__file__).resolve().parents[2]
        with patch.object(subprocess, 'check_output', return_value=OTHER), self.assertRaises(ValueError):
            deployment.verify_checkout(root, SHA)
        with patch.object(subprocess, 'check_output', return_value=SHA), patch.object(
                subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'git diff')), self.assertRaises(subprocess.CalledProcessError):
            deployment.verify_checkout(root, SHA)
        with patch.object(subprocess, 'check_output', return_value=SHA), patch.object(subprocess, 'run'):
            receipt = deployment.verify_checkout(root, SHA)
        self.assertEqual(receipt['pipeline_commit'], SHA)
        self.assertFalse(receipt['budget_reset'])
        self.assertFalse(receipt['competition_release_changed'])

    def test_pending_old_commit_blocks_but_terminal_does_not_migrate(self):
        for status in ('completed', 'baseline_only', 'blocked', 'queued', 'running', 'retry_wait'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as base:
                root = Path(base) / 'worker'
                saved = {'tasks': {'old': {'status': status, 'pipeline_commit': OTHER, 'executions': 2,
                    'forecast': {'probability': .2}}}, 'provider_reservations': [{'kind': 'model'}]}
                write(root / 'state.json', saved)
                before = (root / 'state.json').read_bytes()
                if status in deployment.PENDING:
                    with self.assertRaises(ValueError): deployment.validate_pending(root, Path(base) / 'monitor', {'pipeline_commit': SHA})
                else:
                    self.assertEqual(deployment.validate_pending(root, Path(base) / 'monitor', {'pipeline_commit': SHA})['terminal_cases_preserved'], 1)
                self.assertEqual((root / 'state.json').read_bytes(), before)

    def test_new_monitor_admission_cannot_cross_a_version_switch(self):
        with tempfile.TemporaryDirectory() as base:
            write(Path(base) / 'monitor/state.json', {'tasks': {'new': {'status': 'queued', 'pipeline_commit': OTHER}}})
            with self.assertRaises(ValueError):
                deployment.validate_pending(Path(base) / 'worker', Path(base) / 'monitor', {'pipeline_commit': SHA})

    def test_pending_request_and_recovery_hashes_are_not_reset(self):
        for field in ('request', 'recovery'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as base:
                root = Path(base) / 'worker'
                write(root / 'state.json', {'tasks': {'old': {'status': 'retry_wait', 'executions': 1}}})
                folder = root / 'tasks' / digest('old')
                identity = policy_identity()
                if field == 'request':
                    path = folder / 'collection-request.json'
                    write(path, {'market_capture_identity': identity})
                else:
                    path = folder / 'evidence-capture-v2/identity.json'
                    write(path, identity | {'parent_sha256': 'original'})
                self.assertEqual(deployment.validate_pending(root, Path(base) / 'monitor', {'pipeline_commit': SHA})['pending_checked'], 1)
                saved = read(path)
                if field == 'request': saved['market_capture_identity']['version'] = 'old'
                else: saved['version'] = 'old'
                write(path, saved)
                before = path.read_bytes()
                with self.assertRaises(ValueError): deployment.validate_pending(root, Path(base) / 'monitor', {'pipeline_commit': SHA})
                self.assertEqual(path.read_bytes(), before)

    def test_deployment_record_does_not_rewrite_tasks_or_renew_budget(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            with self.assertRaises(ValueError): deployment.record(root, {'pipeline_commit': SHA})
            write(root / 'state.json', {'tasks': {'old': {'status': 'completed'}}, 'provider_reservations': [1, 2]})
            before = (root / 'state.json').read_bytes()
            receipt = {'pipeline_commit': SHA, 'version': deployment.VERSION, 'budget_reset': False}
            deployment.record(root, receipt)
            first = (root / 'deployments' / (SHA + '.json')).read_bytes()
            deployment.record(root, receipt)
            self.assertEqual((root / 'deployments' / (SHA + '.json')).read_bytes(), first)
            self.assertEqual((root / 'state.json').read_bytes(), before)
            with self.assertRaises(ValueError): deployment.record(root, receipt | {'version': 'changed'})

    def test_new_reader_audit_does_not_reopen_completed_case(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            ledger = {'tasks': {'old': {'status': 'completed', 'forecast': {'probability': .6}, 'executions': 2}}, 'provider_reservations': [1]}
            before = copy.deepcopy(ledger)
            folder = root / 'tasks' / digest('old')
            write(folder / 'analysis-input.json', {'pages': {}})
            with patch('ForecastAgent.market_repricing.materials.prepare', return_value=({'pages': []}, {'empty': True})):
                quarantine_invalid_results(root, ledger)
                first = (folder / 'invalid-result-quarantine.json').read_bytes()
                quarantine_invalid_results(root, ledger)
            self.assertEqual(ledger, before)
            self.assertEqual((folder / 'invalid-result-quarantine.json').read_bytes(), first)
            self.assertTrue(read(folder / 'invalid-result-quarantine.json')['terminal_result_preserved'])

    def test_market_and_competition_workflows_have_separate_controls(self):
        root = Path(__file__).resolve().parents[3]
        for filename in ('market_research_monitor.yaml', 'market_research_worker.yaml'):
            text = (root / '.github/workflows' / filename).read_text()
            self.assertIn('ref: ${{ vars.MARKET_REPRICING_SHA }}', text)
            self.assertIn('python -m ForecastAgent.market_repricing.deployment', text)
            self.assertNotIn('FORECAST_RELEASE_TAG', text)
        competition = (root / '.github/workflows/official_competition.yaml').read_text()
        # The controller may promote competition capture independently of the
        # frozen market implementation; both audited competition pins are valid.
        self.assertRegex(competition, r'ref: v1\.0\.5-(?:recovery\.2|capture\.1|shared\.1|delivery\.1)\b')
        self.assertNotIn('MARKET_REPRICING_SHA', competition)


if __name__ == '__main__':
    unittest.main()
