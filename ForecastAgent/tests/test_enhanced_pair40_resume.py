"""Credential continuation must retain reservations and successful outputs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import enhanced_pair40_resume as resume
from ForecastAgent.analysis.pilot import save, load
from ForecastAgent.tests.test_competition_mercury import response


class ResumeTests(unittest.TestCase):
    def test_quota_retry_uses_remaining_route_attempt(self):
        registry = resume.chain.questions()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'first'
            save(folder/'http/001.json', {'status': 'http_error', 'http_status': 429})
            def decide(state, questions, key, observer):
                record = {'status': 'reserved'}
                token = observer('reserve', record)
                record.update(status='received', response=response(questions))
                observer('complete', record, token)
                return record['response']
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch.object(resume.decisions, 'decide', decide):
                resume.resumed_call({}, folder, registry)
                resume.resumed_call({}, folder, registry)
                self.assertEqual(len(list(folder.glob('http/*.json'))), 2)
                self.assertEqual(load(folder/'http/001.json')['http_status'], 429)
                with self.assertRaisesRegex(RuntimeError, 'lifetime'):
                    resume.resumed_call({}, Path(tmp)/'second', registry)

    def test_uncertain_attempt_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'first'
            save(folder/'http/001.json', {'status': 'reserved'})
            with self.assertRaisesRegex(RuntimeError, '429'):
                resume.resumed_call({}, folder)


if __name__ == '__main__':
    unittest.main()
