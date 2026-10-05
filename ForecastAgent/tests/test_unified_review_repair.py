"""Identical full-state split repair preserves source identity and all quotas."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.experiments import unified_review_repair as repair
from ForecastAgent.tests.test_mercury_material import MercuryMaterial
from ForecastAgent.tests.test_mercury_material_v3 import answers


class RepairTests(unittest.TestCase):
    def fixture(self, parent):
        b, plan, _ = MercuryMaterial().fixture()
        parent.mkdir()
        save(parent / 'manifest.json', {'cases': [{'id': 'case', 'question_id': 101, 'plan': plan}]})
        save(parent / 'tasks/101/analysis-input.json', b)
        save(parent / 'tasks/101/package.json', {'analysis_input_sha256': digest(b)})
        save(parent / 'review-002/case-v7-selection-prepared.json', repair.v7.prepare_selection(b, plan))
        save(parent / 'review-002/state.json', {'limits': {'http': 20, 'logical': 10, 'seconds': 120, 'request_bytes': 500000},
            'calls': [{}, {}], 'attempts': [{'case_id': 'case', 'phase': 'selection', 'http_status': 422}] * 2,
            'elapsed_seconds': 1, 'cases': [{'case_id': 'case', 'pair_complete': False, 'arms': {'v7': {'status': 'failed'}}}]})
        save(parent / 'state.json', {'review_directory': 'review-002', 'cases': [{'id': 'case'}]})
        return b, plan

    def test_exact_full_state_and_prior_reservations_retained(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); b, plan = self.fixture(root / 'parent'); calls = []
            expected = repair.wire.prepare(repair.v7.prepare_selection(b, plan))
            def decide(state, questions, key, observer):
                if next(iter(questions)).startswith('select_'):
                    self.assertEqual(state, expected['state'])
                    self.assertEqual(len(questions), 1)
                calls.append(questions)
                choices = {k: next((c for c in ('NONE', 'unknown', 'unestablished') if c in q['criteria']), next(iter(q['criteria'])))
                           for k,q in questions.items()}
                response = answers(questions, choices)
                rec = {'request': {'model': repair.decisions.MODEL, 'state': state, 'questions': questions}, 'status': 'reserved'}
                token = observer('reserve', rec)
                rec.update(status='received', response=response); observer('complete', rec, token)
                return response
            with patch.object(repair.decisions, 'decide', decide):
                result = repair.run(root / 'parent', root / 'out', 'mock')
            self.assertTrue(result['complete'])
            state = load(root / 'out/review-002/state.json')
            self.assertEqual(state['attempts'][:2], load(root / 'parent/review-002/state.json')['attempts'])
            self.assertEqual(len(state['attempts']), 2 + len(calls))
            self.assertFalse(result['transport_repair']['budgets_reset'])
            self.assertEqual(load(root / 'out/tasks/101/analysis-input.json'), b)

    def test_one_http_failure_blocks_without_extra_head_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.fixture(root / 'parent')
            def fail(state, questions, key, observer):
                rec = {'request': {'model': repair.decisions.MODEL}, 'status': 'reserved'}
                token = observer('reserve', rec)
                rec.update(status='http_error', http_status=422); observer('complete', rec, token)
                raise RuntimeError('Decision endpoint HTTP 422')
            with patch.object(repair.decisions, 'decide', fail):
                result = repair.run(root / 'parent', root / 'out', 'mock')
            self.assertFalse(result['complete'])
            self.assertEqual(len(load(root / 'out/review-002/state.json')['attempts']), 3)


if __name__ == '__main__':
    unittest.main()
