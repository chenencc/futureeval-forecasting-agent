"""Exact marginalization, partial response isolation and preserved originals."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.analysis.distributions import validate_cdf
from ForecastAgent.research_loop import conditional as c, conditional_trial, decision, decision_http, live_trial, state
from ForecastAgent.research_loop.acceptance import accept
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_simple_map import literal_proposal
from ForecastAgent.tests.test_research_live_trial import message
from ForecastAgent.tests.test_competition_mercury import response


def plan(direct=False):
    return {'mode': 'direct' if direct else 'single_pivot', 'reason': 'Demand can affect target revenue.',
        'pivot': {'kind': 'none' if direct else 'world_event', 'entity': '' if direct else 'Company',
            'event': '' if direct else 'Product deliveries exceed the stated guidance threshold',
            'time_window': '' if direct else 'The exact resolving quarter',
            'evidence_node_ids': [] if direct else ['observed_report'],
            'mechanism': '' if direct else 'Higher deliveries increase quarterly revenue.'}}


def mapped(kind='binary', score_plan=None):
    b = source_bundle(kind)
    accept(b, literal_proposal(b), map_protocol=c.PROTOCOL, score_plan=score_plan or plan())
    return b


def answers(prepared, direct=.63, w=.7, yes=.8, no=.25, usability=.9):
    result = response(prepared['questions'])
    if 'event_yes' in result['answers']:
        result['answers']['event_yes']['noul'] = direct
    for key, value in [('pivot_yes', w), ('target_given_pivot', yes),
                       ('target_given_not_pivot', no), ('pivot_usable', usability)]:
        if key in result['answers'] and result['answers'][key]['type'] == 'noul':
            result['answers'][key]['noul'] = value
    return result


class ConditionalTests(unittest.TestCase):
    def test_one_pivot_four_probability_heads_and_one_fallible_diagnostic(self):
        p = c.prepare(mapped())
        self.assertEqual(set(p['questions']), {'event_yes', 'pivot_yes', 'target_given_pivot',
            'target_given_not_pivot', 'pivot_usable'})
        self.assertEqual(set(p['required_questions']), {'event_yes'})
        for key in ['target_given_pivot', 'target_given_not_pivot']:
            self.assertIn('NOT the joint', p['questions'][key]['instructions'])
        self.assertIn('DOES NOT HOLD', p['questions']['target_given_not_pivot']['instructions'])

    def test_raw_marginalization_preserves_zero_one_and_clips_only_payload(self):
        p = c.prepare(mapped()); r = c.evaluate(p, answers(p, direct=1, w=.7, yes=1, no=0))
        self.assertEqual(r['direct']['raw_forecast']['probability_yes'], 1)
        self.assertEqual(r['payload']['probability_yes'], .98)
        self.assertEqual(r['conditional']['branch_not_C']['probability_yes'], 0)
        self.assertAlmostEqual(r['conditional']['raw_forecast']['probability_yes'], .7)
        self.assertFalse(r['conditional']['branch_probabilities_clipped'])
        self.assertFalse(r['conditional']['source_reliability_used_as_weight'])

    def test_shadow_score_never_replaces_direct_or_averages_two_views(self):
        p = c.prepare(mapped()); r = c.evaluate(p, answers(p))
        self.assertAlmostEqual(r['conditional']['raw_forecast']['probability_yes'], .635)
        self.assertEqual(r['payload']['probability_yes'], .63)
        self.assertEqual(r['primary_policy'], 'direct')

    def test_disagreement_and_low_usability_request_review_without_http(self):
        p = c.prepare(mapped()); r = c.evaluate(p, answers(p, direct=.1, usability=.3))
        self.assertTrue(r['review_required'])
        self.assertEqual(r['conditional']['status'], 'needs_review')
        self.assertEqual(set(r['conditional']['review_reasons']),
            {'direct_conditional_disagreement', 'pivot_semantic_usability'})
        self.assertEqual(r['review_http_attempts'], 0)
        self.assertEqual(r['payload']['probability_yes'], .1)

    def test_optional_invalid_or_missing_heads_preserve_valid_direct(self):
        p = c.prepare(mapped())
        for bad in [None, True, float('nan'), -1, 1.1]:
            raw = answers(p)
            if bad is None:
                raw['answers'].pop('target_given_not_pivot')
            else:
                raw['answers']['target_given_not_pivot']['noul'] = bad
            r = c.evaluate(p, raw)
            self.assertEqual(r['conditional']['status'], 'invalid_optional_answers')
            self.assertEqual(r['payload']['probability_yes'], .63)
        raw = answers(p); raw['answers'].pop('event_yes')
        with self.assertRaises(ValueError): c.evaluate(p, raw)

    def test_bad_plan_or_quarantined_references_never_discard_literal_evidence(self):
        for change in [None, {'mode': 'many_pivots'}, {'pivot': {**plan()['pivot'], 'evidence_node_ids': ['unpublished']}},
                       {'pivot': {**plan()['pivot'], 'time_window': ''}},
                       {'pivot': {**plan()['pivot'], 'evidence_node_ids': ['missing']}}]:
            b = source_bundle(); p = plan() if change is not None else None
            if change: p.update(change)
            result = accept(b, literal_proposal(b), map_protocol=c.PROTOCOL, score_plan=p)
            self.assertIn('observed_report', result['acceptance']['accepted_node_ids'])
            self.assertEqual(result['acceptance']['score_plan']['status'], 'rejected')
            prepared = c.prepare(b)
            self.assertEqual(set(prepared['questions']), {'event_yes'})

    def test_direct_plan_and_gap_only_map_do_not_force_a_pivot(self):
        p = c.prepare(mapped(score_plan=plan(True)))
        self.assertEqual(set(p['questions']), {'event_yes'})
        b = source_bundle(); proposed = literal_proposal(b)
        proposed['nodes'] = [proposed['nodes'][2]]; proposed['material_requests'] = []
        accept(b, proposed, map_protocol=c.PROTOCOL, score_plan=plan())
        self.assertEqual(set(c.prepare(b)['questions']), {'event_yes'})

    def test_request_cap_falls_back_without_reselecting_or_removing_original_text(self):
        b = mapped(); original = decision.prepare(b)['baseline']
        with patch.object(c, 'MAX_PLAN_BYTES', 1):
            p = c.prepare(b)
        self.assertEqual(p['plan']['status'], 'direct')
        self.assertEqual(p['state']['evidence'], original['evidence'])
        self.assertEqual(p['state']['sources'], original['sources'])

    def test_all_nonbinary_types_mix_distributions_on_the_same_grid(self):
        for kind in ['multiple_choice', 'numeric', 'date', 'discrete']:
            with self.subTest(kind=kind):
                p = c.prepare(mapped(kind)); raw = answers(p)
                for key, last in [('event_outcome', False), ('target_given_pivot', False),
                                  ('target_given_not_pivot', True)]:
                    keys = list(p['questions'][key]['criteria']); chosen = keys[-1] if last else keys[0]
                    raw['answers'][key] = {'type': 'choice', 'choice': chosen, 'confidence': .9,
                        'probabilities': {k: float(k == chosen) for k in keys}}
                r = c.evaluate(p, raw)
                mix = r['conditional']['raw_forecast']
                if kind == 'multiple_choice':
                    self.assertAlmostEqual(mix['probability_yes_per_category']['A'], .7)
                    self.assertAlmostEqual(sum(mix['probability_yes_per_category'].values()), 1)
                else:
                    self.assertEqual(len(mix['continuous_cdf']), len(r['raw_forecast']['continuous_cdf']))
                    self.assertAlmostEqual(mix['continuous_cdf'][0], .7)
                    validate_cdf(r['conditional']['payload']['continuous_cdf'], p['question'], clipped=True)

    def test_mixture_rejects_mismatched_options_grids_or_nonmonotone_cdf(self):
        for a, b in [({'continuous_cdf': [.2,.1]}, {'continuous_cdf': [0,1]}),
                     ({'continuous_cdf': [0,1]}, {'continuous_cdf': [0,.5,1]}),
                     ({'probability_yes_per_category': {'A': 1}}, {'probability_yes_per_category': {'B': 1}})]:
            with self.assertRaises(ValueError): c.mixture(.5, a, b)

    def test_current_journal_protects_plan_identity_and_no_stale_plan_carryover(self):
        b = mapped(); state.initialize(b)
        b['research_loop']['events'][-1]['acceptance']['score_plan']['plan']['pivot']['entity'] = 'different'
        with self.assertRaisesRegex(ValueError, 'checksum'): c.prepare(b)
        b = mapped(); proposed = literal_proposal(b); proposed['expected_revision'] = 1
        proposed['revision_kind'] = 'interpretation_correction'
        proposed['nodes'][0]['interpretation'] = 'A corrected background interpretation, still not a target outcome.'
        accept(b, proposed, map_protocol='forecast-map-v3')
        self.assertEqual(c.prepare(b)['plan']['status'], 'direct')

    def test_cached_map_retains_plan_without_extra_request_and_raw_pages_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = source_bundle(); before = copy.deepcopy(b); proposed = literal_proposal(b)
            proposed.update(source_reviews=[], score_plan=plan())
            with patch.object(live_trial, 'ask_model', return_value=message(proposed)) as model:
                first = live_trial.map_stage(b, decision.prepare(b)['baseline'], tmp, 'offline', map_protocol=c.PROTOCOL)
                second = live_trial.map_stage(b, decision.prepare(b)['baseline'], tmp, 'offline', map_protocol=c.PROTOCOL)
            self.assertEqual(first, second); self.assertEqual(model.call_count, 1)
            self.assertEqual(first['pages'], before['pages'])
            self.assertEqual(c.prepare(first)['plan']['status'], 'single_pivot')

    def test_partial_http_response_is_preserved_and_resume_charges_no_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = mapped(); p = c.prepare(b); raw = answers(p)
            raw['answers'].pop('target_given_not_pivot')
            class HTTP:
                status = 200
                headers = {}
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self): return json.dumps(raw).encode()
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch.object(
                    decision_http, 'urlopen', return_value=HTTP()) as http:
                first = c.run(b, tmp, execute=True); again = c.run(b, tmp, execute=True)
            self.assertEqual(http.call_count, 1); self.assertEqual(first, again)
            self.assertEqual(first['conditional']['status'], 'invalid_optional_answers')
            self.assertEqual(len(list((Path(tmp)/'decision/http').glob('*.json'))), 1)
            with self.assertRaisesRegex(ValueError, 'Frozen'):
                c.run(mapped(score_plan=plan(True)), tmp, execute=True)

    def test_transport_failure_and_resume_never_reset_a_physical_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = mapped()
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch.object(
                    decision_http, 'urlopen', side_effect=RuntimeError('offline transport failure')) as http:
                with self.assertRaises(RuntimeError): c.run(b, tmp, execute=True)
                with self.assertRaises(RuntimeError): c.run(b, tmp, execute=True)
            self.assertEqual(http.call_count, 1)
            self.assertFalse(load(Path(tmp)/'failure.json')['fabricated_forecast'])
            self.assertEqual(len(list((Path(tmp)/'decision/http').glob('*.json'))), 1)

    def test_trial_reserves_last_attempt_for_direct_score_and_keeps_failed_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); file = root/'input.json'; save(file, source_bundle())
            class HTTP:
                status = 200
                headers = {}
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self): return json.dumps({'model': 'inception/mercury-decide:free',
                    'answers': {'event_yes': {'type': 'noul', 'noul': .62}}}).encode()
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch.object(
                    live_trial, 'ask_model') as model, patch.object(decision_http, 'urlopen', return_value=HTTP()) as http:
                result = conditional_trial.run([file], root/'trial', execute=True, max_http=1)
                again = conditional_trial.run([file], root/'trial', execute=True, max_http=1)
            model.assert_not_called(); self.assertEqual(http.call_count, 1)
            self.assertEqual(result, again)
            self.assertEqual(result['completed'], 1)
            self.assertIn('map_error', result['cases'][0])
            self.assertEqual(result['cases'][0]['direct']['probability_yes'], .62)

    def test_trial_refuses_campaign_budget_overrun_before_any_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); file = root/'input.json'; save(file, source_bundle())
            prior = root/'prior.json'; save(prior, {'usage': {'cumulative_http': 31, 'authorized_cap': 35}})
            with patch.object(live_trial, 'ask_model') as model:
                with self.assertRaisesRegex(ValueError, 'campaign HTTP cap'):
                    conditional_trial.run([file], root/'trial', max_http=5, previous_budget=prior)
            model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
