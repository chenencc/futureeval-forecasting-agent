"""Offline recovery guards for receipt and lifetime budget preservation."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ForecastAgent.market_pulse.recovery import eligible, without_policy


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


if __name__ == '__main__':
    unittest.main()
