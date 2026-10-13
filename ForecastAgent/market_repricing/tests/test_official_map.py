"""Meaningful counterexamples for official-map grounding and bounded transport."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.market_repricing.common import digest
from ForecastAgent.market_repricing.official_map import (
    Citation, GroundedMap, coherence_diagnostics, construct, scoring_state, verify_upstream)


class OfficialMapTests(unittest.TestCase):
    def common(self):
        return {'question': {'title':'Will Parliament pass the bill?',
            'resolution_criteria':'A final vote passing the bill is required.'},
            'sources':[{'source_id':'S1','url':'https://example.org/report','body_sha256':'body'}],
            'evidence':[{'evidence_id':'H1','source_id':'S1','start':20,'end':65,
                'text':'The committee scheduled a hearing next month.'}],
            'as_of_utc':'2026-10-13T10:00:00Z','anchor_at':'2026-10-13T11:00:00Z'}

    def proposal(self):
        return {'nodes':[
            {'title':'Will Parliament pass the bill?','desc':'Exact contract proposition.',
                'category':'goal','basis':'pending','citations':[]},
            {'title':'Committee hearing','desc':'Hearing is scheduled; passage remains uncertain.',
                'category':'milestone','basis':'evidence','citations':[{'evidence_id':'H1',
                    'quote':'The committee scheduled a hearing next month.'}]}],
            'edges':[{'source':'Committee hearing','target':'Will Parliament pass the bill?',
                'type':'evidential','confidence':.7,'rule_quote':''}]}

    def sender(self, proposal, seen):
        def send(payload):
            seen.append(payload)
            return {'choices':[{'message':{'content':json.dumps(proposal)}}],
                'usage':{'total_tokens':100}}
        return send

    def test_pinned_source_and_real_upstream_builder(self):
        self.assertEqual(verify_upstream()['license'],'BSD-2-Clause')
        with tempfile.TemporaryDirectory() as root:
            seen=[]; result=construct(self.common(),root,sender=self.sender(self.proposal(),seen))
            self.assertEqual(result['status'],'accepted')
            self.assertEqual(len(seen),1)
            self.assertEqual(seen[0]['max_tokens'],6000)
            self.assertTrue(result['audit']['causal_sketch_not_bayesian_network'])
            self.assertFalse(result['audit']['semantic_correctness_verified'])

    def test_exact_visible_quote_coordinates_and_unchanged_originals(self):
        with tempfile.TemporaryDirectory() as root:
            common=self.common(); before=copy.deepcopy(common)
            result=construct(common,root,sender=self.sender(self.proposal(),[]))
            state=scoring_state(common,result)
            self.assertEqual(common,before)
            for k,v in common.items(): self.assertEqual(state[k],v)
            binding=next(n['bindings'][0] for n in state['research_map']['nodes'].values() if n['bindings'])
            self.assertEqual((binding['start'],binding['end']),(20,65))
            self.assertEqual(binding['body_sha256'],'body')

    def test_unknown_or_hidden_quote_is_rejected(self):
        for evidence_id,quote in [('HIDDEN','The committee scheduled a hearing next month.'),
                                  ('H1','The bill passed Parliament yesterday.')]:
            with self.subTest(evidence_id=evidence_id), tempfile.TemporaryDirectory() as root:
                p=self.proposal(); p['nodes'][1]['citations']=[{'evidence_id':evidence_id,'quote':quote}]
                result=construct(self.common(),root,sender=self.sender(p,[]))
                self.assertEqual(result['status'],'rejected')
                with self.assertRaises(ValueError): scoring_state(self.common(),result)

    def test_logical_edges_require_exact_rules_not_source_confidence(self):
        with tempfile.TemporaryDirectory() as root:
            p=self.proposal(); p['edges'][0].update(type='necessary',rule_quote='The hearing guarantees passage.')
            result=construct(self.common(),root,sender=self.sender(p,[]))
            self.assertIn('logical_edge_without_exact_rule_binding',result['audit']['errors'])

    def test_cycles_and_reversed_goal_arrows_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            p=self.proposal(); p['edges'].append({'source':'Will Parliament pass the bill?',
                'target':'Committee hearing','type':'causal','confidence':.9,'rule_quote':''})
            result=construct(self.common(),root,sender=self.sender(p,[]))
            self.assertIn('goal_must_be_sink',result['audit']['errors'])
            self.assertIn('directed_cycle',result['audit']['errors'])

    def test_no_silent_loss_of_unknown_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            p=self.proposal(); p['edges'][0]['source']='Invented actor'
            result=construct(self.common(),root,sender=self.sender(p,[]))
            self.assertIn('upstream_dropped_invalid_edge',result['audit']['errors'])

    def test_cache_cannot_change_coverage_or_hide_extra_requests(self):
        with tempfile.TemporaryDirectory() as root:
            seen=[]; sender=self.sender(self.proposal(),seen)
            first=construct(self.common(),root,sender=sender)
            self.assertEqual(construct(self.common(),root,sender=sender),first)
            self.assertEqual(len(seen),1)
            different=self.common(); different['evidence'][0]['text']+=' Later news.'
            with self.assertRaises(ValueError): construct(different,root,sender=sender)

    def test_formal_repair_is_one_additional_physical_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            seen=[]
            def sender(payload):
                seen.append(payload)
                return {'choices':[{'message':{'content':'invalid JSON' if len(seen)==1 else json.dumps(self.proposal())}}]}
            result=construct(self.common(),root,sender=sender)
            self.assertEqual(result['status'],'accepted'); self.assertEqual(len(seen),2)

    def test_repeated_invalid_output_stops_and_stays_cached(self):
        with tempfile.TemporaryDirectory() as root:
            seen=[]
            def sender(payload):
                seen.append(payload); return {'choices':[{'message':{'content':'invalid JSON'}}]}
            result=construct(self.common(),root,sender=sender)
            self.assertEqual(result['status'],'failed'); self.assertEqual(len(seen),2)
            construct(self.common(),root,sender=sender)
            self.assertEqual(len(seen),2)

    def test_mutated_graph_cannot_reach_decision_model(self):
        with tempfile.TemporaryDirectory() as root:
            result=construct(self.common(),root,sender=self.sender(self.proposal(),[]))
            result['graph']['nodes']['committee_hearing']['desc']='Now this falsely asserts passage.'
            with self.assertRaises(ValueError): scoring_state(self.common(),result)

    def test_coherence_is_an_optional_mathematical_diagnostic(self):
        raw={'A':.4,'~A':.6,'B':.8,'B|A':.6,'B|~A':.1}
        value=coherence_diagnostics(raw); p=value['projected']
        self.assertLess(abs(p['A']+p['~A']-1),1e-4)
        self.assertLess(abs(p['B']-p['A']*p['B|A']-p['~A']*p['B|~A']),1e-4)
        self.assertGreater(value['maximum_adjustment'],.01)
        self.assertFalse(value['forecast_replaced'])
        for wrong in (raw|{'A':True},raw|{'A':float('nan')}, {'A':.4}):
            with self.assertRaises(ValueError): coherence_diagnostics(wrong)


if __name__=='__main__':
    unittest.main()
