"""Coverage selection must retain original provenance and frozen budgets."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis import mercury_coverage_chain as chain
from ForecastAgent.analysis.pilot import load
from ForecastAgent.tests.test_mercury_evidence_chain import bundle, response


class CoverageTests(unittest.TestCase):
    def test_exact_table_recall_and_duplicate_omission(self):
        original = bundle()
        original['request'].update(question='Will Acme desktop share exceed 8% in August 2026?',
            resolution_criteria='Worldwide Acme desktop share in August 2026 must exceed 8%.')
        body = ('Home Contact Privacy Help About\n' * 800) + '| August 2026 | Acme desktop share worldwide | 8.2% |\n'
        original['pages'] = {f'https://example.org/{i}': {'content': body,
                            'content_sha256': hashlib.sha256(body.encode()).hexdigest()} for i in range(2)}
        packet = chain.baseline.full_packet(original)
        state, audit = chain.select(packet)
        self.assertTrue(any('8.2%' in s['text'] for s in state['evidence']))
        self.assertTrue(audit['duplicate_span_ids'])
        self.assertLessEqual(audit['request_bytes'], chain.baseline.FIRST_BYTES)
        for s in state['evidence']:
            self.assertEqual(s['text'], body[s['start']:s['end']])
        second, _ = chain.select(packet, state, ['time_window'], chain.baseline.SECOND_BYTES)
        self.assertTrue(all(s in second['evidence'] for s in state['evidence']))

    def test_failed_attempt_and_resume_do_not_reset(self):
        with tempfile.TemporaryDirectory() as root, patch.object(chain.baseline, 'call', return_value=response(True)) as mocked:
            result = chain.run_task(bundle(), root)
            self.assertLessEqual(mocked.call_count, 2)
            self.assertEqual(result['http_attempt_cap'], 2)
            original = load(Path(root)/'first-state.json')
            self.assertNotIn('resolution', original)
            self.assertNotIn('answers', original)
            changed = bundle()
            changed['request']['resolution_criteria'] += ' changed'
            with self.assertRaisesRegex(ValueError, 'Frozen'):
                chain.run_task(changed, root)


if __name__ == '__main__':
    unittest.main()
