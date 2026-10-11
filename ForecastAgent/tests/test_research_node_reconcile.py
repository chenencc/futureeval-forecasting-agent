"""Source-isolated review must preserve references, budgets and unreviewed nodes."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import node_reconcile as nr, post_supplement as post, runtime, state
from ForecastAgent.tests.test_research_reference_map import proposal, apply
from ForecastAgent.tests.test_research_target_logic import fixture
from ForecastAgent.tests.test_research_gap_feedback import task


class NodeReconcileTests(unittest.TestCase):
    def sample(self):
        b, _ = fixture()
        p = proposal(b)
        apply(b, p)
        original = b['research_loop']['current']['nodes'][0]
        packet = {'evidence': copy.deepcopy(original['bindings']), 'context_evidence': [],
                  'context_reference_ids': {}, 'material_sha256': state.catalog(b)['material_sha256']}
        # Build the same model-facing map contract without changing task state.
        from ForecastAgent.research_loop import reference_map, target_logic, simple_map, delta, gap_feedback
        schema = reference_map.schema(target_logic.schema(simple_map.MAP_SCHEMA, bundle=b))
        schema = delta.schema(schema)
        schema = gap_feedback.schema(schema)
        mt = {'type': 'function', 'function': {'parameters': schema}}
        card = nr.cards(b, packet)[0]
        answer = {k: copy.deepcopy(card['candidate'][k]) for k in
                  ('evidence_ids', 'interpretation', 'limitation', 'applicability', 'target_links')}
        answer.update(node_id=card['node_id'], verdict='retained', review_reason='Own supplied source reviewed.')
        return b, packet, card, mt, answer

    def test_request_excludes_other_nodes_and_their_originals(self):
        b, packet, card, mt, answer = self.sample()
        unrelated = copy.deepcopy(packet['evidence'][0])
        unrelated.update(evidence_id='R_other', url='https://other.example/data', body_sha256='b'*64,
                         text='Other outlet reports a different event.')
        packet['evidence'].append(unrelated)
        card = nr.cards(b, packet)[0]
        data = json.loads(nr.messages(b, card)[1]['content'])
        self.assertNotIn('current_map', data)
        self.assertNotIn('R_other', json.dumps(data))
        self.assertNotIn('Other outlet reports', json.dumps(data))
        self.assertFalse(card['interpretation_verified'])

    def test_unknown_or_cross_node_reference_is_rejected(self):
        b, packet, card, mt, answer = self.sample()
        answer['evidence_ids'] = ['R_other']
        with self.assertRaisesRegex(ValueError, 'own supplied scope'):
            nr.proposal(b, packet, card, answer, mt)

    def test_retained_verdict_cannot_hide_an_edit(self):
        b, packet, card, mt, answer = self.sample()
        answer['interpretation'] = 'Changed without declaring correction.'
        with self.assertRaisesRegex(ValueError, 'Retained verdict'):
            nr.proposal(b, packet, card, answer, mt)

    def test_deferred_is_not_an_accepted_review_or_map_edit(self):
        b, packet, card, mt, answer = self.sample()
        answer.update(verdict='deferred', evidence_ids=[])
        before = copy.deepcopy(b)
        p, audit = nr.proposal(b, packet, card, answer, mt)
        self.assertIsNone(p)
        self.assertEqual(audit['status'], 'deferred')
        self.assertFalse(audit['meaning_verified'])
        self.assertEqual(b, before)

    def test_valid_correction_is_a_merge_without_source_receipts_or_truth_claim(self):
        b, packet, card, mt, answer = self.sample()
        answer.update(verdict='corrected', interpretation='Historical measurement; future result remains unknown.')
        p, audit = nr.proposal(b, packet, card, answer, mt)
        self.assertEqual(p['update_mode'], 'merge')
        self.assertEqual(p['material_reviews'], [])
        self.assertEqual(p['retired_node_ids'], [])
        self.assertEqual(p['nodes'][0]['evidence_ids'], card['own_reference_ids'])
        self.assertFalse(audit['meaning_verified'])
        self.assertTrue(audit['changed'])

    def test_post_review_is_one_node_per_call_and_cached_reentry_spends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = task(Path(tmp)/'original')
            p = proposal(t.bundle)
            apply(t.bundle, p)
            t.bundle['request_hash'] = hashlib.sha256(json.dumps(t.bundle['request'], sort_keys=True).encode()).hexdigest()
            t.save()
            original = copy.deepcopy(t.bundle)
            seen = []
            def model(messages, key, **kwargs):
                self.assertEqual(kwargs['forced_tool'], nr.NAME)
                data = json.loads(messages[1]['content'])
                card = data['observation_to_review']; seen.append(card)
                answer = {k: copy.deepcopy(card['candidate'][k]) for k in
                          ('evidence_ids', 'interpretation', 'limitation', 'applicability', 'target_links')}
                answer.update(node_id=card['node_id'], verdict='corrected',
                    interpretation='Historical measurement; future outcome unknown.', review_reason='Narrowed to own evidence.')
                observer = kwargs['observer']; record = {'status': 'reserved', 'request': {'model': 'test'}}
                token = observer('reserve', record)
                record.update(status='received', response={'usage': {'total_tokens': 10}})
                observer('complete', record, token)
                return {'tool_calls': [{'function': {'name': nr.NAME, 'arguments': json.dumps(answer)}}]}
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test'}), patch.object(post, 'ask_model', side_effect=model) as calls:
                child, report = post.run(original, Path(tmp)/'review', node_by_node=True, consistency_review=True)
                again, cached = post.run(original, Path(tmp)/'review', node_by_node=True, consistency_review=True)
            self.assertEqual(calls.call_count, 1)
            self.assertEqual(report['status'], 'nodes_reviewed')
            self.assertEqual(child, again); self.assertEqual(report, cached)
            self.assertFalse(report['steps'][0]['node_review']['meaning_verified'])
            for key in post.PRESERVED:
                self.assertEqual(child.get(key), original.get(key))
            self.assertEqual(report['usage']['totals']['http_attempts'], 1)


if __name__ == '__main__':
    unittest.main()
