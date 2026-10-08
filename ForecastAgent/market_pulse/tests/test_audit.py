"""Offline tests for issuer-neutral acquisition audits and receipt provenance."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ForecastAgent.competition.queue import save
from ForecastAgent.market_pulse.audit import run


class AuditTests(unittest.TestCase):
    def fixture(self, root, searches=0):
        runner = b'runpy.run_path("transport")\nfrom ForecastAgent.inventory import snapshot\n'
        (root / 'executed-runner-source.py').write_bytes(runner)
        save(root / 'manifest.json', {'runner_sha256': hashlib.sha256(runner).hexdigest(),
            'financial_customization': 'Versioned profile', 'rows': [
                {'id': '123', 'input': 'inputs/123.json', 'input_sha256': 'test', 'title': 'Revenue (Amazon)'}]})
        save(root / 'inputs/123.json', {'question': 'Revenue (Amazon)', 'resolution_criteria': 'First official release',
            'question_type': 'numeric', 'unit': '$'})
        save(root / 'tasks/123/collection-result.json', {'status': 'exported'})
        save(root / 'tasks/123/retrieval/release-1.0.5/collection/bundle.json', {'pages': {},
            'searches': [{'query': 'issuer'}] * searches, 'model_attempts': []})

    def test_single_new_issuer_and_archived_preimport_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            result = run(root)
            self.assertEqual(result['questions'][0]['issuer'], 'Amazon')
            self.assertTrue(result['transport_preimport_installation'])
            self.assertEqual(result['shared_capture_hashes'], [])
            self.assertFalse(result['analysis_run'])
            self.assertFalse(result['submitted'])

    def test_caps_use_actual_ledger_even_without_resource_summary(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, searches=4)
            result = run(root)
            self.assertEqual(result['logical_tavily_basic'], 4)
            self.assertFalse(result['tavily_limit_respected'])


if __name__ == '__main__':
    unittest.main()
