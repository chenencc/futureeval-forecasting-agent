"""The unseen trial freezes code, source windows, labels, model and limits."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.experiments.field_paired_unseen import MANIFEST, ROOT, MODEL, run, validate_freeze, frozen_prompt
from ForecastAgent.supplement.material_contract import evaluate
from ForecastAgent.supplement.field_contract import span_catalog


class PairedUnseenTests(unittest.TestCase):
    def test_frozen_code_exclusions_and_prelabels(self):
        data = json.loads(MANIFEST.read_text(encoding='utf-8'))
        validate_freeze(data)
        cases = data['cases']
        self.assertEqual(len(cases), 10)
        ids = {c['question_id'] for c in cases}
        self.assertEqual(len(ids), 10)
        self.assertFalse(ids & set(data['excluded_question_ids']))
        self.assertEqual(sum(c['expected'] == 'matched' for c in cases), 5)
        for case in cases:
            source = case['source']
            self.assertEqual(hashlib.sha256(source['text'].encode()).hexdigest(), case['provenance']['window_sha256'])
            self.assertEqual(len(source['text']), source['end'] - source['start'])
            self.assertEqual(''.join(s['text'] for s in span_catalog(source)), source['text'])
            self.assertEqual(evaluate(case['contract'], source, case['observation'])['status'], case['expected'])
            self.assertEqual(set(case['contract']['required_axes']),
                             {f['axis'] for f in case['contract']['required_fields']})
        self.assertTrue(frozen_prompt(ROOT / 'ForecastAgent/experiments/field_contract_round.py',
                                     'Review saved material only.').startswith('Review saved material only.'))

    def test_identical_blinded_inputs_alternating_order_and_no_reset(self):
        inputs = []
        names = []

        def ask(messages, key, **kwargs):
            packet = json.loads(messages[-1]['content'])
            inputs.append(messages[-1]['content'])
            self.assertFalse({'expected', 'observation', 'question_id', 'provenance'} & set(packet))
            name = kwargs['forced_tool']
            names.append(name)
            self.assertEqual(kwargs['reasoning'], {'max_tokens': 512})
            self.assertEqual(kwargs['max_output_tokens'], 1800 if name == 'observe_material' else 4096)
            token = kwargs['observer']('reserve', {'status': 'reserved', 'request': {'model': MODEL}})
            kwargs['observer']('complete', {'status': 'received', 'request': {'model': MODEL},
                'response': {'choices': [{'finish_reason': 'stop'}], 'usage': {'total_tokens': 10}}}, token)
            observed = {'document_role': 'unknown', 'fit_axes': {a: False for a in
                ('entity', 'material_type', 'metric', 'period')}, 'evidence_relation': 'unknown',
                'reason': 'Insufficient supported judgment.'}
            if name == 'observe_material':
                observed['quote'] = packet['source']['text'][:100]
            else:
                observed.update(span_ids=['span-001'], material_verdict='unknown',
                                explanation_verdict='unknown', field_evidence=[])
            return {'content': json.dumps(observed)}

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'FORECAST_MODEL': MODEL, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), patch(
                'ForecastAgent.providers.model.ask_model', side_effect=ask):
            folder = Path(tmp) / 'pair'
            result = run(folder, 'dummy')
            self.assertEqual(result['actual_http_attempts'], 20)
            for i in range(0, 20, 2):
                self.assertEqual(inputs[i], inputs[i+1])
            self.assertEqual(names[:4], ['observe_material', 'observe_fields', 'observe_fields', 'observe_material'])
            blind = json.loads((folder / 'blinded-review.json').read_text())
            self.assertTrue(all('arm' not in row and 'expected' not in row for row in blind))
            with self.assertRaisesRegex(ValueError, 'no_implicit_reset'):
                run(folder, 'dummy')

    def test_provider_failure_preserves_reservation_and_stops(self):
        def unavailable(messages, key, **kwargs):
            token = kwargs['observer']('reserve', {'status': 'reserved', 'request': {'model': MODEL}})
            kwargs['observer']('complete', {'status': 'failed', 'request': {'model': MODEL}}, token)
            raise RuntimeError('service_unavailable')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'FORECAST_MODEL': MODEL, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), patch(
                'ForecastAgent.providers.model.ask_model', side_effect=unavailable) as model:
            folder = Path(tmp) / 'pair'
            result = run(folder, 'dummy')
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result['unknown_usage_attempts'], 1)
            self.assertTrue(json.loads((folder / 'state.json').read_text())['blocked'])
