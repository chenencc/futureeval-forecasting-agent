"""Trial journals reserve capacity, hide labels and forbid implicit reruns."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.experiments.field_contract_round import run, MODEL, LIMITS


class FieldTrialTests(unittest.TestCase):
    def test_labels_hidden_and_journal_not_restarted(self):
        packets = []

        def ask(messages, key, **kwargs):
            packet = json.loads(messages[-1]['content'])
            packets.append(packet)
            self.assertFalse({'expected', 'observation', 'question_id'} & set(packet))
            token = kwargs['observer']('reserve', {'status': 'reserved', 'request': {'model': MODEL}})
            kwargs['observer']('complete', {'status': 'received', 'request': {'model': MODEL},
                'response': {'choices': [{'finish_reason': 'stop'}], 'usage': {'total_tokens': 10}}}, token)
            return {'content': json.dumps({'document_role': 'unknown', 'evidence_relation': 'unknown',
                'fit_axes': {a: False for a in ['entity', 'metric', 'material_type', 'period']},
                'quote': packet['source']['text'][:100], 'reason': 'No supported judgment.',
                'material_verdict': 'unknown', 'explanation_verdict': 'unknown', 'field_evidence': []})}

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                'FORECAST_MODEL': MODEL, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), patch(
                'ForecastAgent.providers.model.ask_model', side_effect=ask):
            output = Path(temp) / 'trial'
            report = run(output, 'dummy')
            self.assertEqual(report['actual_http_attempts'], LIMITS['logical'])
            self.assertEqual(report['known_tokens'], 100)
            self.assertEqual(report['unknown_usage_attempts'], 0)
            self.assertEqual(len(packets), 10)
            with self.assertRaisesRegex(ValueError, 'no_implicit_reset'):
                run(output, 'dummy')

    def test_service_failure_preserves_state_and_stops(self):
        def unavailable(messages, key, **kwargs):
            token = kwargs['observer']('reserve', {'status': 'reserved', 'request': {'model': MODEL}})
            kwargs['observer']('complete', {'status': 'failed', 'request': {'model': MODEL}}, token)
            raise RuntimeError('service_unavailable')

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                'FORECAST_MODEL': MODEL, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), patch(
                'ForecastAgent.providers.model.ask_model', side_effect=unavailable) as model:
            output = Path(temp) / 'trial'
            result = run(output, 'dummy')
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result['unknown_usage_attempts'], 1)
            self.assertTrue(json.loads((output / 'state.json').read_text())['blocked'])
