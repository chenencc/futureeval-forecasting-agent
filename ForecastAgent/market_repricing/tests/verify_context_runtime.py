"""Pinned-engine reading-page, protected-rule and non-HTTP handoff gates."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.market_repricing.runtime import activate
activate(os.environ['MARKET_ENGINE_DIRECTORY'])
from ForecastAgent.market_repricing.common import read, write
from ForecastAgent.market_repricing.context_delivery import adapter, fit_messages
from ForecastAgent.market_repricing.budget_runtime import native_accounting
from ForecastAgent.runtime.context import ContextProjectionError, encode
from ForecastAgent.research_loop import delivery
from ForecastAgent.releases import v1_0_5 as release


class ContextRuntime(unittest.TestCase):
    def test_whole_span_page_keeps_unicode_quotes_coordinates_and_all_rules(self):
        spans = [{'evidence_id': f'R{i}', 'material_id': 'M1', 'text': '原文 α😀 | 12.4 |\n' * 30,
            'url': 'https://example.org/a', 'body_sha256': 'a' * 64,
            'start': i * 800, 'end': i * 800 + 450} for i in range(4)]
        payload = {'immutable_question': {'question': 'Literal target', 'resolution_criteria': 'Exact rules',
                   'fine_print': 'Exact exception'}, 'existing_node_ids': ['n1'], 'targets': [{'id': 'F1'}],
            'current_nodes': [{'id': 'n1', 'kind': 'observation', 'claim': 'Original R1', 'interpretation': 'x' * 5000}],
            'research_binding_frame': {'material_sha256': 'm' * 64, 'revision': 2,
              'inspected_evidence': spans, 'source_context': [], 'gap_feedback': {
              'pending_material_count': 2, 'pending_materials': [
                 {'material_id': 'M1', 'scope': 'Partial', 'scope_sha256': 's' * 64},
                 {'material_id': 'M2', 'scope': 'Historical navigation ' * 500}],
              'gaps': [{'gap_id': 'g1', 'meaning_verified': False, 'reason': 'Future unknown'}]}}}
        before = copy.deepcopy(payload)
        messages = [{'role': 'system', 'content': 'Full system'}, {'role': 'user', 'content': encode(payload)}]
        result, receipt = fit_messages(messages, payload, 3000)
        current = json.loads(result[1]['content'])
        self.assertEqual(payload, before)
        self.assertEqual(result[0], messages[0])
        self.assertLessEqual(len(encode(result)), 3000)
        for field in ('immutable_question', 'targets', 'existing_node_ids'):
            self.assertEqual(current[field], before[field])
        frame = current['research_binding_frame']
        self.assertEqual(frame['gap_feedback']['gaps'], before['research_binding_frame']['gap_feedback']['gaps'])
        self.assertEqual(frame['gap_feedback']['pending_materials'][0], before['research_binding_frame']['gap_feedback']['pending_materials'][0])
        for span in frame['inspected_evidence']: self.assertIn(span, spans)
        self.assertTrue(frame['inspected_evidence'])
        self.assertEqual(set(receipt['delivered_evidence_ids'] + receipt['omitted_evidence_ids']), {s['evidence_id'] for s in spans})
        self.assertFalse(receipt['navigation_page']['processing_receipt_created'])

    def test_unfit_rule_or_single_span_fails_locally_without_truncation(self):
        for payload in ({'immutable_question': {'resolution_criteria': 'x' * 4000}},
                        {'immutable_question': {'resolution_criteria': 'Complete rule'},
                         'research_binding_frame': {'inspected_evidence': [{'evidence_id': 'R1', 'text': 'x' * 4000}]}}):
            before = copy.deepcopy(payload)
            with self.assertRaises(ContextProjectionError):
                fit_messages([{'role': 'system', 'content': 'System'}, {'role': 'user', 'content': ''}], payload, 1000)
            self.assertEqual(payload, before)

    def test_successful_native_fit_is_unchanged_and_adapter_restores_hook(self):
        payload = {'immutable_question': {'resolution_criteria': 'Complete rule'}}
        messages = [{'role': 'system', 'content': 'System'}, {'role': 'user', 'content': encode(payload)}]
        expected = delivery.fit_messages(messages, payload, 28000)
        self.assertEqual(fit_messages(messages, payload, 28000), expected)
        original = delivery.fit_messages
        with tempfile.TemporaryDirectory() as folder:
            with adapter(folder):
                self.assertEqual(delivery.fit_messages(messages, payload, 28000), expected)
            self.assertIs(delivery.fit_messages, original)
            receipt = read(Path(folder) / 'context-delivery/projection-0001.json')
            self.assertFalse(receipt['provider_delivery_confirmed'])
            self.assertFalse(receipt['budget_reset'])

    def source(self, folder, status='received'):
        from ForecastAgent.market_repricing.tests.test_budget_recovery import BudgetRecovery
        denied = BudgetRecovery().denial(folder)
        native = denied.parent.parent
        bundle = read(native / 'bundle.json')
        path = native / 'model_calls/2.json'
        write(path, {'status': status, 'started_at_utc': '2026-10-14T09:01:00Z'})
        bundle['model_attempts'].append({'path': 'model_calls/2.json', 'status': status,
                                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        bundle.update(pages={'https://example.org/a': {'content': 'Literal readable source',
                     'body_diagnostics': {'usable_text': True}}}, request={},
            result={'status': 'partial', 'termination_reason': 'context_projection_failure', 'incomplete': True,
                    'execution_report': {'owner': 'program', 'interrupted': True}})
        write(native / 'bundle.json', bundle)
        return bundle, native

    def test_proven_budget_denial_does_not_block_existing_context_raw_handoff_gate(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root); bundle, native = self.source(folder)
            before = (native / 'bundle.json').read_bytes()
            self.assertFalse(release.raw_handoff_eligible(bundle))
            with native_accounting(folder): self.assertTrue(release.raw_handoff_eligible(bundle))
            self.assertEqual((native / 'bundle.json').read_bytes(), before)
            self.assertFalse(release.raw_handoff_eligible(bundle))
            audit = read(folder / 'raw-handoff-budget-audit.json')
            self.assertFalse(audit['semantic_complete'])
            self.assertEqual(len(audit['excluded_non_http_receipts']), 1)

    def test_real_failure_or_unknown_reservation_never_gets_refunded_for_handoff(self):
        for status in ('http_error', 'reserved'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as root:
                folder = Path(root); bundle, _ = self.source(folder, status)
                with native_accounting(folder): self.assertFalse(release.raw_handoff_eligible(bundle))


if __name__ == '__main__': unittest.main()
