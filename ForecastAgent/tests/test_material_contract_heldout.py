"""Validate preregistered labels and unchanged V2 evaluator before live calls."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ForecastAgent.experiments.material_contract_round import replay, MODEL, BUDGET

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'ForecastAgent/experiments/MATERIAL_CONTRACT_HELDOUT10.json'


class HeldoutTests(unittest.TestCase):
    def test_frozen_evaluators_model_budget_and_exclusion(self):
        data = json.loads(MANIFEST.read_text(encoding='utf-8'))
        self.assertEqual(data['model'], MODEL)
        self.assertEqual(data['budget'], BUDGET)
        for file, expected in data['frozen_git_blob_sha256'].items():
            content = (ROOT / file).read_bytes().replace(b'\r\n', b'\n')
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected, file)
        cases = data['cases']
        self.assertEqual(len(cases), 10)
        ids = {c['question_id'] for c in cases}
        self.assertEqual(len(ids), 10)
        self.assertFalse(ids & set(data['excluded_question_ids']))
        self.assertEqual(sum(c['expected'] == 'matched' for c in cases), 5)
        for c in cases:
            text = c['source']['text']
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), c['provenance']['window_sha256'])
            self.assertEqual(c['source']['end'] - c['source']['start'], len(text))
            self.assertIn(c['observation']['quote'], text)

    def test_prelabels_are_consistent_without_model_calls(self):
        with tempfile.TemporaryDirectory() as output:
            report = replay(output, MANIFEST)
        self.assertEqual(report['new_correct'], 10)
        self.assertEqual(report['new_false_accepts'], 0)
        self.assertEqual(report['new_false_rejects'], 0)
        self.assertEqual(report['model_calls'], 0)
