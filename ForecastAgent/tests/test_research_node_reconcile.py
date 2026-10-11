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

    def test_record_context_requires_delivered_overlap_and_same_representation(self):
        b, packet, card, mt, answer = self.sample()
        own = packet['evidence'][0]
        record = copy.deepcopy(own)
        record.update(evidence_id='R_record', record_delimiter='multiline_markdown_link_end',
                      start=max(0, own['start']-1), end=own['end']+1)
        packet['context_evidence'] = [record]
        card = nr.cards(b, packet)[0]
        self.assertIn('R_record', card['selectable_reference_ids'])
        self.assertFalse(card['interpretation_verified'])
        for field, value in [('url', 'https://other.example/record'),
                             ('body_sha256', 'f'*64), ('view_sha256', 'e'*64),
                             ('coordinate_space', 'different_view')]:
            changed = copy.deepcopy(packet)
            changed['context_evidence'][0][field] = value
            self.assertNotIn('R_record', nr.cards(b, changed)[0]['selectable_reference_ids'])
        changed = copy.deepcopy(packet)
        changed['context_evidence'][0].update(start=own['end'], end=own['end']+10)
        self.assertNotIn('R_record', nr.cards(b, changed)[0]['selectable_reference_ids'])
        packet['context_evidence'] = []
        self.assertNotIn('R_record', nr.cards(b, packet)[0]['selectable_reference_ids'])

    def test_retained_verdict_edit_is_measured_without_dropping_valid_annotation(self):
        b, packet, card, mt, answer = self.sample()
        answer['interpretation'] = 'Changed without declaring correction.'
        p, audit = nr.proposal(b, packet, card, answer, mt)
        self.assertEqual(audit['model_verdict'], 'retained')
        self.assertEqual(audit['verdict'], 'corrected')
        self.assertTrue(audit['verdict_field_mismatch'])
        self.assertIn('interpretation', audit['changed_fields'])
        self.assertFalse(audit['meaning_verified'])
        self.assertEqual(p['nodes'][0]['interpretation'], answer['interpretation'])

    def test_blind_request_hides_candidate_annotations_and_verdict(self):
        b, packet, card, mt, answer = self.sample()
        card['candidate']['interpretation'] = 'OLD_UNSUPPORTED_PHOTO_CLAIM'
        card['candidate']['limitation'] = 'OLD_COVERAGE_CLAIM'
        card['candidate']['target_links'][0]['reason'] = 'OLD_LINK_CLAIM'
        data = json.loads(nr.messages(b, card, blind=True)[1]['content'])
        self.assertNotIn('candidate', data['observation_to_review'])
        self.assertNotIn('OLD_', json.dumps(data))
        self.assertEqual(data['observation_to_review']['originals'], card['originals'])
        schema = nr.tool(card, mt, blind=True)['function']['parameters']
        self.assertNotIn('verdict', schema['properties'])

    def test_premise_audit_is_own_source_only_advisory_and_hidden_in_blind_mode(self):
        from ForecastAgent.research_loop import predictive_focus
        b, packet, card, mt, answer = self.sample()
        b['request'][predictive_focus.FIELD] = predictive_focus.POLICY
        b['request']['resolution_criteria'] = 'Target cohort on July 20, 2026 has 18 members.'
        card['candidate']['interpretation'] = '18 members as of July 20, 2026.'
        # An unrelated delivered source with those numbers cannot certify this
        # candidate, and nearby context not actually selected is not a binding.
        other = copy.deepcopy(card['originals'][0])
        other.update(evidence_id='R_other', text='18 members July 20, 2026.',
                     url='https://other.example/cohort')
        card['originals'].append(other)
        data = json.loads(nr.messages(b, card)[1]['content'])
        audit = data['candidate_literal_origins']
        self.assertFalse(audit['meaning_verified'])
        self.assertFalse(audit['originals_rejected_by_risk_signals'])
        self.assertEqual(len(audit['node_review_signals']), 1)
        origins = {r['literal']:r['origin'] for r in audit['node_review_signals'][0]['numeric_literal_origins']}
        self.assertEqual(origins['18'], 'rule_only')
        self.assertEqual(origins['20'], 'rule_only')
        self.assertNotIn('candidate_literal_origins', json.loads(nr.messages(b,card,blind=True)[1]['content']))

    def test_blind_translation_measures_edits_and_preserves_reference_binding(self):
        b, packet, card, mt, answer = self.sample()
        answer.pop('verdict')
        answer['interpretation'] = 'Caption describes a start; image content is not supplied.'
        p, audit = nr.proposal(b, packet, card, answer, mt, blind=True)
        self.assertTrue(audit['blind_original_reading'])
        self.assertIsNone(audit['model_verdict'])
        self.assertEqual(audit['verdict'], 'corrected')
        self.assertEqual(p['material_reviews'], [])
        self.assertFalse(audit['meaning_verified'])

    def test_original_only_phase_hides_rule_and_prior_anchors_without_implicit_links(self):
        b, packet, card, mt, answer = self.sample()
        b['request']['question'] = 'TARGET_ANCHOR'
        b['request']['resolution_criteria'] = 'RULE_ANCHOR'
        card['candidate']['interpretation'] = 'OLD_ANCHOR'
        messages = nr.messages(b,card,blind=True,original_only=True)
        data = json.loads(messages[1]['content'])
        for key in ('question','as_of_utc','target_conditions'):
            self.assertNotIn(key, data)
        for word in ('TARGET_ANCHOR','RULE_ANCHOR','OLD_ANCHOR'):
            self.assertNotIn(word, messages[1]['content'])
        self.assertEqual(data['observation_to_review']['originals'], card['originals'])
        self.assertIn('target_links=[]', messages[0]['content'])
        with self.assertRaisesRegex(ValueError,'blind mode'):
            nr.messages(b,card,original_only=True)
        with self.assertRaisesRegex(ValueError,'blind mode'):
            nr.messages(b,card,blind=True,original_only=True,format_feedback={'draft':answer})
        answer.pop('verdict')
        with self.assertRaises(ValueError):
            nr.proposal(b,packet,card,answer,mt,blind=True,original_only=True)
        answer['target_links'] = []
        proposal, audit = nr.proposal(b,packet,card,answer,mt,blind=True,original_only=True)
        self.assertEqual(proposal['nodes'][0]['target_links'], [])
        self.assertFalse(audit['meaning_verified'])

    def test_blind_still_rejects_overlong_annotations(self):
        b, packet, card, mt, answer = self.sample()
        answer.pop('verdict'); answer['interpretation'] = 'x'*241
        with self.assertRaises(ValueError):
            nr.proposal(b, packet, card, answer, mt, blind=True)

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

    def test_blind_post_wiring_and_frozen_mode_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = task(Path(tmp)/'original'); apply(t.bundle, proposal(t.bundle))
            t.bundle['request_hash'] = hashlib.sha256(json.dumps(t.bundle['request'], sort_keys=True).encode()).hexdigest()
            t.save(); original = copy.deepcopy(t.bundle)
            def model(messages, key, **kwargs):
                data = json.loads(messages[1]['content'])
                self.assertNotIn('candidate', data['observation_to_review'])
                self.assertNotIn('verdict', kwargs['tools'][0]['function']['parameters']['properties'])
                packet = post.reading_packet(t, consistency_review=True)
                card = nr.cards(t.bundle, packet)[0]
                answer = {k:copy.deepcopy(card['candidate'][k]) for k in
                    ('evidence_ids','interpretation','limitation','applicability','target_links')}
                answer.update(node_id=card['node_id'], review_reason='Source-local observation reconstructed.')
                record = {'status':'reserved','request':{'model':'test'}}
                token = kwargs['observer']('reserve', record)
                record.update(status='received',response={'usage':{'total_tokens':10}})
                kwargs['observer']('complete', record, token)
                return {'tool_calls':[{'function':{'name':nr.NAME,'arguments':json.dumps(answer)}}]}
            with patch.dict('os.environ', {'OPENROUTER_API_KEY':'test'}), patch.object(post,'ask_model',side_effect=model) as calls:
                child, report = post.run(original, Path(tmp)/'review',node_by_node=True,
                    consistency_review=True,blind_node_read=True)
                again, cached = post.run(original, Path(tmp)/'review',node_by_node=True,
                    consistency_review=True,blind_node_read=True)
                with self.assertRaisesRegex(ValueError,'changed'):
                    post.run(original, Path(tmp)/'review',node_by_node=True,consistency_review=True)
            self.assertEqual(calls.call_count,1)
            self.assertEqual(report['status'],'nodes_reviewed')
            self.assertTrue(report['blind_node_read_requested'])
            self.assertTrue(report['steps'][0]['node_review']['blind_original_reading'])
            self.assertEqual(child,again); self.assertEqual(report,cached)
            for key in post.PRESERVED:
                self.assertEqual(child.get(key), original.get(key))


if __name__ == '__main__':
    unittest.main()
