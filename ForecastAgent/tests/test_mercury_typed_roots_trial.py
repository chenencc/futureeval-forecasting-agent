"""Identical input coverage, durable reservations and no allowance reset."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.experiments import mercury_typed_roots_trial as trial
from ForecastAgent.tests.test_mercury_material_v4 import Obligations
from ForecastAgent.tests.test_mercury_material_v3 import answers


class Trial(unittest.TestCase):
    def fixture(self, root):
        b, plan, _, p = Obligations().fixture()
        manifest = {'trial_id': 'offline', 'frozen_code_sha256': {},
            'limits': {'logical': 8, 'http': 8, 'seconds': 30, 'request_bytes': 500000},
            'prior_experiments': [{'run': 'previous', 'http': 65, 'unchanged': True}],
            'cases': [{'id': 'fixed', 'question_id': 'fixture', 'bundle': b,
                'plan': plan, 'contracts': p['contracts'], 'arm_order': ['v4', 'v5']}]}
        save(root / 'manifest.json', manifest)
        return root / 'manifest.json'

    def decide(self, state, questions, key, observer):
        selected = {k: ('NONE' if 'NONE' in q['criteria'] else next(iter(q['criteria'])))
            for k, q in questions.items()}
        response = answers(questions, selected)
        rec = {'request': {'model': trial.decisions.MODEL, 'state': state, 'questions': questions}, 'status': 'reserved'}
        token = observer('reserve', rec)
        rec.update(status='received', response=response)
        observer('complete', rec, token)
        return response

    def test_three_calls_shared_input_preserved_history_and_no_reset(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = self.fixture(root)
            with patch.object(trial.decisions, 'decide', self.decide):
                r = trial.run(root / 'out', 'dummy', manifest)
            self.assertTrue(r['business_gate_passed'])
            self.assertEqual(r['actual_http_attempts'], 3)
            self.assertEqual(r['prior_experiments'][0]['http'], 65)
            for field in ('reading', 'needs', 'question', 'rule_catalog'):
                old = load(root / 'out/fixed-v4-prepared.json')['state'][field]
                new = load(root / 'out/fixed-v5-prepared.json')['state'][field]
                self.assertEqual(old, new)
            with self.assertRaisesRegex(ValueError, 'reset_budget'):
                trial.run(root / 'out', 'dummy', manifest)

    def test_failure_preserves_reservation_and_stops(self):
        def fail(state, questions, key, observer):
            observer('reserve', {'request': {'model': trial.decisions.MODEL}, 'status': 'reserved'})
            raise RuntimeError('service_failure')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(trial.decisions, 'decide', fail):
                r = trial.run(root / 'out', 'dummy', self.fixture(root))
            self.assertTrue(r['blocked'])
            self.assertEqual(r['actual_http_attempts'], 1)
            self.assertEqual(r['attempts'][0]['status'], 'reserved')

    def test_resume_skips_completed_arms_and_rejects_changed_history(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = self.fixture(root)
            with patch.object(trial.decisions, 'decide', self.decide):
                old = trial.run(root / 'out', 'dummy', manifest)
            with patch.object(trial.decisions, 'decide', side_effect=AssertionError('no extra call')):
                new = trial.run(root / 'continued', 'dummy', manifest, resume=root / 'out')
            self.assertEqual(new['attempts'], old['attempts'])
            self.assertEqual(new['calls'], old['calls'])
            altered = load(manifest)
            altered['limits']['http'] += 1
            save(root / 'changed.json', altered)
            with self.assertRaisesRegex(ValueError, 'wrong_parent_identity'):
                trial.run(root / 'invalid', 'dummy', root / 'changed.json', resume=root / 'out')


if __name__ == '__main__':
    unittest.main()
