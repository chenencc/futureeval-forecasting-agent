"""Regression risks in literal maps, optional edges and original-only fallback."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.research_loop import decision, live_trial, simple_map, state
from ForecastAgent.research_loop.acceptance import accept
from ForecastAgent.tests.test_research_loop import proposal, source_bundle
from ForecastAgent.tests.test_research_live_trial import message


def literal_proposal(bundle):
    result = proposal(bundle)
    for node in result['nodes']:
        node.update(claim_origin='source_quote' if node['kind'] == 'observation' else 'gap' if node['kind'] == 'unknown' else 'hypothesis',
            interpretation='', event_stage='unknown', applicability='unknown', limitation='', episode_id='')
    result['nodes'][0].update(claim='Revenue was 24 billion dollars.',
        interpretation='This describes revenue in the saved report.', event_stage='completed',
        applicability='background', limitation='The final target release is not established.', episode_id='old-quarter')
    result['relations'] = []
    return result


class SimpleMapTests(unittest.TestCase):
    def accept(self, bundle, proposed):
        return accept(bundle, proposed, map_protocol=simple_map.PROTOCOL)

    def test_literal_observation_and_scope_reach_mercury_without_changing_originals(self):
        b = source_bundle(); before = copy.deepcopy(b); p = literal_proposal(b)
        self.accept(b, p); prepared = decision.prepare(b)
        node = prepared['enriched']['research_map']['nodes'][0]
        self.assertEqual(node['claim'], 'Revenue was 24 billion dollars.')
        self.assertEqual(node['applicability'], 'background')
        self.assertIn('not established', node['limitation'])
        self.assertEqual(prepared['enriched']['research_map']['map_protocol'], simple_map.PROTOCOL)
        self.assertTrue(b['research_loop']['current']['nodes'][0]['literal_quote_verified'])
        self.assertFalse(b['research_loop']['current']['nodes'][0]['interpretation_verified'])
        self.assertEqual(b['pages'], before['pages'])
        self.assertTrue(all(prepared['enriched'][k] == v for k, v in prepared['baseline'].items()))

    def test_invented_or_changed_observation_is_isolated_not_silently_rewritten(self):
        b = source_bundle(); p = literal_proposal(b)
        bad = copy.deepcopy(p['nodes'][0]); bad.update(id='changed_tense', claim='Revenue will be 24 billion dollars.')
        p['nodes'].insert(0, bad)
        result = self.accept(b, p)['acceptance']
        self.assertEqual(result['rejected'][0]['node_id'], 'changed_tense')
        self.assertIn('literal quote', result['rejected'][0]['error'])
        self.assertNotIn('changed_tense', result['accepted_node_ids'])
        self.assertIn('observed_report', result['accepted_node_ids'])
        self.assertTrue(result['narratives_omitted'])

    def test_zero_edges_and_inline_limitation_need_no_extra_unknown(self):
        b = source_bundle(); p = literal_proposal(b); p['nodes'] = p['nodes'][:1]
        p['material_requests'] = []
        self.accept(b, p)
        self.assertEqual(len(b['research_loop']['current']['nodes']), 1)
        self.assertEqual(b['research_loop']['current']['relations'], [])

    def test_empty_narrative_does_not_discard_literal_observations(self):
        b = source_bundle(); p = literal_proposal(b); raw = copy.deepcopy(p)
        for key in ('supporting_path', 'alternative_path', 'revision_reason'):
            p[key] = ''
        frozen = copy.deepcopy(p)
        report = self.accept(b, p)['acceptance']
        self.assertEqual(report['accepted_node_ids'], [n['id'] for n in raw['nodes']])
        self.assertEqual(len(report['empty_narratives_defaulted']), 3)
        self.assertEqual(b['research_loop']['current']['nodes'][0]['claim'], raw['nodes'][0]['claim'])
        self.assertEqual(p, frozen)
        self.assertTrue(all(b['research_loop']['current'][k] for k in report['empty_narratives_defaulted']))

    def test_unjustified_strong_edges_are_quarantined_individually(self):
        for kind, extra, error in [('causal', {}, 'mechanism'),
                                   ('necessary', {'mechanism': '', 'rule_quote': 'invented rule'}, 'immutable rule')]:
            with self.subTest(kind=kind):
                b = source_bundle(); p = literal_proposal(b)
                p['relations'] = [{'from_id': 'observed_report', 'to_id': 'demand', 'kind': kind,
                    'rationale': 'The events coincide.', 'mechanism': '', 'rule_quote': '', **extra}]
                report = self.accept(b, p)['acceptance']
                self.assertIn(error, report['rejected'][0]['error'])
                self.assertEqual(b['research_loop']['current']['relations'], [])
                self.assertEqual(len(report['accepted_node_ids']), 3)

    def test_repeat_grouping_is_not_independence_or_semantic_certification(self):
        b = source_bundle(); p = literal_proposal(b)
        duplicate = copy.deepcopy(p['nodes'][0]); duplicate['id'] = 'second_report'
        p['nodes'].append(duplicate)
        self.accept(b, p)
        groups = decision.prepare(b)['enriched']['research_map']['evidence_groups']
        self.assertEqual(groups['repeated_quote_groups'], [['observed_report', 'second_report']])
        self.assertFalse(groups['independence_verified'])
        self.assertEqual(groups['episode_hypotheses'][0]['episode_id'], 'old-quarter')
        # Stage remains explicitly fallible even for a mechanically valid quote.
        p = literal_proposal(source_bundle()); p['nodes'][0]['event_stage'] = 'planned'
        other = source_bundle(); self.accept(other, p)
        self.assertFalse(other['research_loop']['current']['nodes'][0]['interpretation_verified'])

    def test_gap_inventory_retained_but_scoring_inputs_are_identical(self):
        b = source_bundle(); p = literal_proposal(b); p['nodes'] = p['nodes'][2:]
        self.accept(b, p); prepared = decision.prepare(b)
        self.assertEqual(b['research_loop']['events'][-1]['acceptance']['status'], 'gap_only')
        self.assertEqual(prepared['baseline'], prepared['enriched'])
        self.assertNotIn('research_map', prepared['enriched'])

    def test_bad_map_no_format_retry_and_resume_consumes_no_new_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = source_bundle(); baseline = decision.prepare(b)['baseline']
            def fake(*args, **kwargs):
                token = kwargs['observer']('reserve', {'status': 'reserved'})
                kwargs['observer']('complete', {'status': 'received'}, token)
                return {'role': 'assistant', 'content': 'malformed map'}
            with patch.object(live_trial, 'ask_model', side_effect=fake) as model:
                for _ in range(2):
                    with self.assertRaises(ValueError):
                        live_trial.map_stage(b, baseline, tmp, 'test', map_protocol=simple_map.PROTOCOL)
                self.assertEqual(model.call_count, 1)
            self.assertEqual(len(list((Path(tmp)/'http').glob('*.json'))), 1)
            self.assertEqual(load(Path(tmp)/'rejection-0.json')['format_correction_calls'], 0)

    def test_map_failure_or_missing_reference_returns_score_without_duplicate_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); parent = root/'input.json'; save(parent, source_bundle())
            def score(bundle, folder, *, execute, arm):
                self.assertEqual(arm, 'baseline')
                result = {'status': 'completed', 'payload': {'probability_yes': .4}, 'audit': {}}
                save(folder/'baseline-result.json', result)
                return result
            with (patch.object(live_trial, 'map_stage', side_effect=ValueError('No complete reference span')),
                 patch.object(live_trial, 'visible_catalog', side_effect=ValueError('No complete reference span')),
                 patch.object(decision, 'run', side_effect=score) as provider,
                 patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test'})):
                report = live_trial.run([parent], root/'run', True, map_protocol=simple_map.PROTOCOL)
            case = report['cases'][0]
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(case['status'], 'paired_completed')
            self.assertTrue(case['enriched']['reused_original_decision'])
            self.assertFalse(case['map_supplied_to_scoring'])
            self.assertEqual(case['baseline']['forecast'], case['enriched']['forecast'])
            self.assertEqual(case['enriched_usage']['totals']['new_http_attempts'], 0)
            self.assertTrue(case['parent_preserved'])

    def test_map_score_failure_reuses_successful_direct_score_without_a_third_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); parent = root/'input.json'; save(parent, source_bundle())
            def mapped(child, *args, **kwargs):
                self.accept(child, literal_proposal(child))
                return child
            def score(bundle, folder, *, execute, arm):
                if arm == 'enriched':
                    save(folder/'enriched/http/001.json', {'status': 'http_error', 'http_status': 429})
                    raise RuntimeError('Decision endpoint HTTP 429')
                result = {'status': 'completed', 'payload': {'probability_yes': .4}, 'audit': {}}
                save(folder/'baseline-result.json', result)
                return result
            with (patch.object(live_trial, 'map_stage', side_effect=mapped),
                  patch.object(decision, 'run', side_effect=score) as provider,
                  patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test'})):
                case = live_trial.run([parent], root/'run', True, map_protocol=simple_map.PROTOCOL)['cases'][0]
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(case['status'], 'paired_completed')
            self.assertTrue(case['map_supplied_to_scoring'])
            self.assertFalse(case['final_score_used_map'])
            self.assertTrue(case['enriched']['reused_original_decision'])
            self.assertIn('429', case['map_scoring_error'])
            self.assertEqual(case['enriched_usage']['totals']['http_attempts'], 1)
            self.assertEqual(case['enriched_usage']['totals']['unknown_reported_cost_usd_attempts'], 1)


if __name__ == '__main__':
    unittest.main()
