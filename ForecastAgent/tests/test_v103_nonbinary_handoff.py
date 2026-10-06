"""Typed packing equivalence, scale integrity, scoring and durable fallback."""
import copy
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import context_handoff as original, typed_context_handoff as delivery
from ForecastAgent.acquisition import nonbinary_handoff_inputs as shared, v103_nonbinary_handoff_trial as trial
from ForecastAgent.analysis import mercury_evidence_chain as chain, mercury_nonbinary_trial as typed
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.tests.test_material_handoff import fixture


class NonbinaryHandoffTests(TestCase):
    def test_explicit_registry_adapter_keeps_frozen_binary_packing(self):
        bundle = fixture('Dated 2026 USD report.\n\nAlpha and Beta original paragraph.\n\n' * 150)
        for old,new in ((original.baseline,delivery.baseline),(original.pack,delivery.pack)):
            a,_ = old(bundle)
            b,_ = new(bundle,chain.questions())
            self.assertEqual(a,b)
        a,_ = original.pack(bundle)
        b,_ = delivery.pack(bundle,chain.questions())
        old,_ = original.extend(bundle,a,['time_window'])
        new,_ = delivery.extend(bundle,b,['time_window'],chain.questions())
        self.assertEqual(old,new)

    def test_metadata_fill_preserves_originals_and_rejects_true_scale_changes(self):
        row = next(r for r in load(typed.INPUT) if r['type']=='numeric')
        old = typed.request(row)
        old['background'] = 'Original archived background.'
        old['scaling'].pop('continuous_range',None)
        before = copy.deepcopy(old)
        merged = shared.request(old,row)
        self.assertEqual(old,before)
        self.assertEqual(merged['background'],before['background'])
        self.assertEqual(merged['scaling'],row['scaling'])
        old['scaling']['range_max'] += 1
        with self.assertRaises(ValueError):
            shared.request(old,row)

    def test_all_typed_forecasts_and_metrics_have_exact_expected_formats(self):
        labels = load(trial.LABELS)['records']
        valid = research = 0
        for row in load(typed.INPUT):
            spec = shared.spec(typed.request(row),row)
            response = {'answers':{'event_outcome':{'probabilities':{k:1/len(spec['criteria']) for k in spec['criteria']}}}}
            forecast = typed.forecast(response,spec)
            score = shared.score(forecast,spec,labels[str(row['post_id'])])
            self.assertEqual(score['payload_format_valid'],row['type']!='date')
            valid += score['payload_format_valid']
            research += not score['payload_format_valid']
        self.assertEqual((valid,research),(17,3))

    def test_bounded_crps_matches_analytic_uniform_distribution_and_censored_tails(self):
        self.assertAlmostEqual(shared.bounded_crps([[0,0],[1,1]],.5),1/12)
        self.assertAlmostEqual(shared.bounded_crps([[0,0],[1,1]],float('-inf')),1/3)
        self.assertAlmostEqual(shared.bounded_crps([[0,0],[1,1]],float('inf')),1/3)

    def test_failed_second_read_keeps_first_and_sealed_replay_spends_nothing(self):
        spec = {'kind':'multiple_choice','options':['A','B'],'criteria':{'option_0':'A','option_1':'B'},'official_metadata_available':True}
        response = {'answers':{'event_outcome':{'probabilities':{'option_0':.25,'option_1':.75}}}}
        bundle = fixture('Alpha and Beta report.\n\n' * 800)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(trial.chain,'call',side_effect=[response,RuntimeError('Unavailable')]) as provider, \
                 patch.object(trial.typed,'route',return_value=['time_window']):
                result = trial.run_arm(bundle,folder,'release',spec)
                replay = trial.run_arm(bundle,folder,'release',spec)
            self.assertEqual(provider.call_count,2)
            self.assertEqual(result,replay)
            self.assertEqual(result['status'],'completed_with_reread_failure')
            self.assertEqual(result['forecast'],result['first_forecast'])
            altered = load(folder/'result.json')
            altered['forecast']['clipped_probabilities']['A'] = .9
            save(folder/'result.json',altered)
            with self.assertRaises(ValueError):
                trial.run_arm(bundle,folder,'release',spec)

    def test_failed_first_stage_is_sealed_without_manufacturing_a_distribution(self):
        spec = {'kind':'multiple_choice','options':['A','B'],'criteria':{'option_0':'A','option_1':'B'},'official_metadata_available':True}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(trial.chain,'call',side_effect=RuntimeError('Account quota')) as provider:
                first = trial.run_arm(fixture('Original report.'),Path(directory),'context',spec)
                replay = trial.run_arm(fixture('Original report.'),Path(directory),'context',spec)
            self.assertEqual(provider.call_count,1)
            self.assertEqual(first,replay)
            self.assertIsNone(first['forecast'])
