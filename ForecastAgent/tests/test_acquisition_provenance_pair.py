"""Fixed-input paired-review and durable inherited-budget gates."""
import copy
import unittest
from ForecastAgent.experiments import acquisition_provenance_pair as pair
from ForecastAgent.tests import test_acquisition_provenance as fixtures


class PairedReview(unittest.TestCase):
    def test_same_reading_and_needs_reach_both_arms_without_replanning(self):
        request, plan, pages, _, row = fixtures.Provenance().fixture()
        calls = []
        def execute(phase, prompt, payload, tool):
            calls.append((phase, copy.deepcopy(payload)))
            if phase == 'annotate_provenance':
                return {'annotations': [row]}
            return {'annotations': [{k: row[k] for k in
                ('need_id', 'passage_ids', 'relation', 'fit', 'observation', 'explanation')}]}
        bundle = {'request': request, 'pages': pages, 'frozen_plan': plan, 'arm_order': ['v2', 'v3']}
        result = pair.agent_review(bundle, execute, lambda value: None)
        self.assertEqual(len(calls), 2)
        for key in ('question', 'needs', 'reading', 'selected_character_budget'):
            self.assertEqual(calls[0][1][key], calls[1][1][key])
        self.assertEqual(result['application_status'], 'reviewed_with_gaps')
        self.assertEqual(result['arms']['v2']['coverage'][0]['state'], 'covered_by_model_claim')
        self.assertEqual(result['arms']['v3']['coverage'][0]['condition_coverage'], 'unverified')

    def test_empty_or_malformed_second_arm_cannot_count_as_paired_success(self):
        request, plan, pages, _, _ = fixtures.Provenance().fixture()
        result = pair.agent_review({'request': request, 'pages': pages,
            'frozen_plan': plan, 'arm_order': ['v3', 'v2']},
            lambda *args: {'annotations': []}, lambda value: None)
        self.assertEqual(result['application_status'], 'partial_review')

    def test_manifest_preserves_original_caps_and_latest_parent(self):
        import json
        from pathlib import Path
        data = json.loads((Path(pair.__file__).with_name('ACQUISITION_PROVENANCE_PAIR2.json')).read_text())
        self.assertEqual(data['parent_run_id'], '37220371858')
        self.assertEqual(data['parent_consumption'], {'logical': 16, 'http': 16})
        self.assertEqual(data['new_limits']['logical'], 4)
        self.assertLessEqual(16 + data['new_limits']['http'], 30)
        self.assertEqual(len(data['cases']), 2)
        self.assertEqual(data['cases'][0]['bundle']['arm_order'], ['v2', 'v3'])
        self.assertEqual(data['cases'][1]['bundle']['arm_order'], ['v3', 'v2'])

    def test_pair_restores_usage_and_never_exceeds_original_logical_cap(self):
        import hashlib
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from ForecastAgent.analysis.pilot import save
        from ForecastAgent.experiments import acquisition_contract_live as live
        request, plan, pages, _, row = fixtures.Provenance().fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); parent = root / 'parent'; parent.mkdir()
            old = {'decisions': [{'status': 'received'} for _ in range(16)],
                   'attempts': [{'status': 'received', 'usage': {'total_tokens': 1}} for _ in range(16)],
                   'cases': [], 'blocked': False}
            save(parent / 'state.json', old); save(parent / 'identity.json', {'fixed': True})
            save(parent / 'preserved.txt', {'original': True})
            data = {'scope': 'offline cumulative pair test', 'frozen_code_sha256': {},
                'parent_run_id': 'fixed', 'parent_consumption': {'logical': 16, 'http': 16},
                'parent_file_sha256': {p: hashlib.sha256((parent/p).read_bytes()).hexdigest()
                    for p in ['state.json', 'identity.json']}, 'new_limits': {'logical': 4, 'http': 8},
                'cases': [{'id': str(i), 'question_id': str(i), 'bundle': {'request': request,
                    'pages': pages, 'frozen_plan': plan, 'arm_order': ['v2', 'v3']}} for i in range(3)]}
            save(root / 'manifest.json', data)
            def ask(messages, key, *, observer, forced_tool, **kwargs):
                provenance = 'origin:' in messages[0]['content']
                record = copy.deepcopy(row) if provenance else {k: row[k] for k in
                    ('need_id', 'passage_ids', 'relation', 'fit', 'observation', 'explanation')}
                message = {'tool_calls': [{'function': {'name': forced_tool,
                    'arguments': json.dumps({'annotations': [record]})}}]}
                transport = {'status': 'reserved', 'request': {'model': live.MODEL}}
                token = observer('reserve', transport)
                transport.update(status='received', response={'usage': {'total_tokens': 2},
                    'choices': [{'message': message, 'finish_reason': 'tool_calls'}]})
                observer('complete', transport, token)
                return message
            with patch.dict(os.environ, {'FORECAST_MODEL': live.MODEL, 'FORECAST_MODEL_FALLBACK_SUPER': '0'}), \
                    patch('ForecastAgent.providers.model.ask_model', ask):
                result = live.run(root/'output', 'dummy', root/'manifest.json', provenance_pair=True, parent=parent)
            self.assertTrue(result['blocked'])  # A third pair cannot consume another call.
            self.assertEqual(result['logical_decisions'], 20)
            self.assertEqual(result['actual_http_attempts'], 20)
            new = json.loads((root/'output/state.json').read_text())
            self.assertEqual(new['attempts'][:16], old['attempts'])
            self.assertEqual(new['decisions'][:16], old['decisions'])
            self.assertEqual((root/'output/preserved.txt').read_bytes(), (parent/'preserved.txt').read_bytes())


if __name__ == '__main__':
    unittest.main()
