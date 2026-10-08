"""Offline recovery guards for receipt and lifetime budget preservation."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ForecastAgent.market_pulse.recovery import eligible, without_policy, seed_history, release_request


class RecoveryTests(unittest.TestCase):
    def fixture(self, root):
        raw = b'{"status":"received"}'
        (root / 'receipt.json').write_bytes(raw)
        request = {'id': '123', 'question': 'A financial question'}
        return {'request': request, 'request_hash': hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()).hexdigest(),
            'result': {'termination_reason': 'stalled'}, 'sessions': [{'id': 1}],
            'model_attempts': [{'status': 'received', 'path': 'receipt.json',
                                'sha256': hashlib.sha256(raw).hexdigest()}]}

    def test_empty_failure_keeps_model_receipts_and_execution_count(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); bundle = self.fixture(root)
            before = json.dumps(bundle, sort_keys=True)
            eligible(bundle, root)
            self.assertEqual(before, json.dumps(bundle, sort_keys=True))

    def test_acquired_or_exhausted_tasks_are_not_reopened(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for key in ('pages', 'searches', 'exa_searches', 'fetch_attempts'):
                bundle = self.fixture(root); bundle[key] = ['existing']
                with self.assertRaisesRegex(ValueError, 'resource consumption'):
                    eligible(bundle, root)
            bundle = self.fixture(root); bundle['sessions'] *= 3
            with self.assertRaisesRegex(ValueError, 'three-execution'):
                eligible(bundle, root)

    def test_altered_original_journal_is_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); bundle = self.fixture(root)
            (root / 'receipt.json').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'journal changed'):
                eligible(bundle, root)

    def test_only_policy_is_removed_for_identity_comparison(self):
        self.assertEqual(without_policy({'question': 'immutable', 'limit': 3,
            'financial_acquisition_policy': {'old': True}}), {'question': 'immutable', 'limit': 3})

    def test_generic_history_template_uses_validator_without_resetting_failures(self):
        from ForecastAgent.acquisition.pipeline import prepare
        from ForecastAgent.market_pulse.collection import prepare as financial_prepare
        from ForecastAgent.runtime.retrieval import RetrievalTask
        from ForecastAgent.competition.queue import load
        with TemporaryDirectory() as folder:
            root = Path(folder)
            request, _ = prepare(release_request(financial_prepare({
                'id': '123', 'question': 'First earnings per share? (Apple)',
                'resolution_criteria': 'Use the first official GAAP diluted EPS release.',
                'background': '', 'fine_print': ''})))
            task = RetrievalTask(root, request)
            task.bundle.update(financial_recovery={'budget_reset': False},
                result={'incomplete': True, 'resumable': True},
                sessions=[{'id': 1}, {'id': 2}])
            task.bundle['control']['material_plan_failures'] = [{'error': 'prior invalid plan'}] * 2
            task.bundle['control']['material_plan_stop'] = 'plan_repair_limit'
            task.save()
            before = load(root / 'bundle.json')
            seed_history(root)
            after = load(root / 'bundle.json')
            self.assertIn('GAAP diluted earnings per share', after['plan'][0]['condition'])
            for key in ('model_attempts', 'sessions', 'acquisition_limits', 'request'):
                self.assertEqual(before.get(key), after.get(key))
            self.assertEqual(before['control']['material_plan_failures'], after['control']['material_plan_failures'])
            self.assertNotIn('material_plan_stop', after['control'])
            self.assertFalse(after['financial_recovery']['program_history_template']['semantic_verified'])


if __name__ == '__main__':
    unittest.main()
