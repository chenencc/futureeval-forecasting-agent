"""Verify cumulative reservations and truthful application gates without HTTP."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import save
from ForecastAgent.experiments import acquisition_contract_live as live
from ForecastAgent.tests import test_acquisition_ids as fixtures


class IDPilot(unittest.TestCase):
    def prepare(self, root):
        parent = root / 'parent'; parent.mkdir()
        state = {'attempts': [{'status': 'received', 'usage': {'total_tokens': 1}} for _ in range(10)],
            'decisions': [{'status': 'received'} for _ in range(10)], 'cases': [], 'blocked': False}
        save(parent / 'state.json', state); save(parent / 'identity.json', {'parent': 'fixed'})
        bundle, _ = fixtures.AcquisitionIDs().fixture()
        manifest = {'frozen_code_sha256': {}, 'scope': 'offline budget test',
            'parent_run_id': 'fixed', 'parent_file_sha256': {name: hashlib.sha256((parent / name).read_bytes()).hexdigest()
                for name in ('state.json', 'identity.json')}, 'parent_consumption': {'logical': 10, 'http': 10},
            'new_limits': {'logical': 2, 'http': 2}, 'cases': [{'id': 'case', 'question_id': 'fixture', 'bundle': bundle}]}
        path = root / 'manifest.json'; save(path, manifest)
        return parent, path, state

    def fake(self, empty=False):
        def ask(messages, key, *, tools, forced_tool, observer, **kwargs):
            payload = json.loads(messages[1]['content'])
            if forced_tool == 'propose_material_needs':
                result = {'needs': [] if empty else [{'id': 'stage', 'condition': 'Permit issuance',
                    'critical': True, 'dimension': 'stage', 'target': 'issued',
                    'rule_ids': [payload['rule_catalog'][0]['rule_id']]}]}
            else:
                result = {'annotations': [{'need_id': 'stage',
                    'passage_ids': [payload['reading']['passages'][0]['passage_id']],
                    'relation': 'counterevidence', 'fit': 'applicable', 'observation': 'pending', 'explanation': 'Pending.'}]}
            message = {'tool_calls': [{'function': {'name': forced_tool, 'arguments': json.dumps(result)}}]}
            record = {'status': 'reserved', 'request': {'model': live.MODEL}}
            token = observer('reserve', record)
            record.update(status='received', response={'choices': [{'message': message, 'finish_reason': 'tool_calls'}],
                                                       'usage': {'total_tokens': 2}})
            observer('complete', record, token)
            return message
        return ask

    def test_parent_usage_and_records_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); parent, manifest, previous = self.prepare(root)
            with patch.dict(os.environ, {'FORECAST_MODEL': live.MODEL, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), \
                    patch('ForecastAgent.providers.model.ask_model', self.fake()):
                report = live.run(root / 'output', 'dummy-key', manifest, ids_pilot=True, parent=parent)
            self.assertEqual(report['actual_http_attempts'], 12)
            self.assertEqual(report['logical_decisions'], 12)
            self.assertEqual(report['new_http_attempts'], 2)
            self.assertEqual(report['known_tokens'], 14)
            self.assertTrue(report['business_gate_passed'])
            state = json.loads((root / 'output/state.json').read_text())
            self.assertEqual(state['attempts'][:10], previous['attempts'])
            self.assertEqual(state['decisions'][:10], previous['decisions'])
            self.assertEqual(json.loads((parent / 'state.json').read_text()), previous)

    def test_empty_plan_fails_business_gate_even_with_received_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); parent, manifest, _ = self.prepare(root)
            with patch.dict(os.environ, {'FORECAST_MODEL': live.MODEL, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), \
                    patch('ForecastAgent.providers.model.ask_model', self.fake(empty=True)):
                report = live.run(root / 'output', 'dummy-key', manifest, ids_pilot=True, parent=parent)
            self.assertEqual(report['new_http_attempts'], 1)
            self.assertFalse(report['business_gate_passed'])
            self.assertEqual(report['cases'][0]['status'], 'failed_requirements')

    def test_changed_parent_stops_before_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); parent, manifest, _ = self.prepare(root)
            save(parent / 'identity.json', {'parent': 'changed'})
            with patch.dict(os.environ, {'FORECAST_MODEL': live.MODEL, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), \
                    patch('ForecastAgent.providers.model.ask_model') as ask:
                with self.assertRaisesRegex(ValueError, 'parent_identity_or_state_changed'):
                    live.run(root / 'output', 'dummy-key', manifest, ids_pilot=True, parent=parent)
                ask.assert_not_called()


if __name__ == '__main__':
    unittest.main()
