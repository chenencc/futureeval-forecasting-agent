"""Meaningful experiment controls for real calls, failure and preservation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.research_loop import decision
from ForecastAgent.research_loop import live_trial as trial
from ForecastAgent.research_loop.evaluate_trial import cdf_loss
from ForecastAgent.tests.test_research_loop import source_bundle, proposal


def message(p):
    return {'role': 'assistant', 'tool_calls': [{'id': 'm', 'type': 'function',
        'function': {'name': 'update_research_state', 'arguments': json.dumps(p)}}]}


class LiveTrialTests(unittest.TestCase):
    def test_bound_map_uses_only_original_visible_coverage(self):
        b = source_bundle(); common = decision.prepare(b)['baseline']
        _, refs = trial.visible_catalog(b, common)
        p, child = trial.parse_map(message(proposal(b)), b, [r['evidence_id'] for r in refs])
        both = decision.prepare(child)
        self.assertTrue(all(v == both['enriched'][k] for k, v in both['baseline'].items()))
        self.assertEqual(both['baseline'], common)
        with self.assertRaisesRegex(ValueError, 'outside common'):
            trial.parse_map(message(p), b, [])

    def test_single_binding_vocabulary_preserves_every_original_character(self):
        b=source_bundle(); common=decision.prepare(b)['baseline']
        view=trial.map_original_view(b,common)
        for segment in common['evidence']:
            pieces=[s for s in view['evidence'] if s['source_id']==segment['source_id']
                    and segment['start']<=s['start']<s['end']<=segment['end']]
            self.assertEqual(''.join(s['text'] for s in pieces),segment['text'])
        self.assertTrue(all(s.get('evidence_id','R').startswith('R') for s in view['evidence']))
        self.assertEqual(view['question'],common['question'])

    def test_baseline_import_refuses_changed_raw_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); parent=root/'old/cases/x/decision/baseline'
            save(parent/'request.json',{'state':{'changed':True},'questions':{}})
            with self.assertRaisesRegex(ValueError,'identical original'):
                trial.import_baseline(root/'old','x',{}, {},root/'new')

    def test_format_correction_shares_http_cap_and_success_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = source_bundle(); common = decision.prepare(b)['baseline']; calls = []
            def fake(messages, key, **kwargs):
                record = {'status': 'reserved', 'request': {'model': trial.SUPER}}
                token = kwargs['observer']('reserve', record); calls.append(messages)
                kwargs['observer']('complete', {**record, 'status': 'received'}, token)
                return {'role': 'assistant', 'content': 'bad'} if len(calls) == 1 else message(proposal(b))
            with patch.object(trial, 'ask_model', side_effect=fake):
                trial.map_stage(b, common, tmp, 'test')
                trial.map_stage(b, common, tmp, 'test')
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(list((Path(tmp)/'http').glob('*.json'))), 2)
            self.assertEqual(b['pages'], source_bundle()['pages'])

    def test_failed_requests_are_not_replenished_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = source_bundle(); common = decision.prepare(b)['baseline']
            def fake(messages, key, **kwargs):
                for _ in range(2):
                    record = {'status': 'reserved'}
                    token = kwargs['observer']('reserve', record)
                    kwargs['observer']('complete', {**record, 'status': 'http_error'}, token)
                raise RuntimeError('Service unavailable')
            with patch.object(trial, 'ask_model', side_effect=fake) as provider:
                with self.assertRaises(RuntimeError): trial.map_stage(b, common, tmp, 'test')
                with self.assertRaisesRegex(RuntimeError, 'lifetime cap'): trial.map_stage(b, common, tmp, 'test')
                self.assertEqual(provider.call_count, 1)

    def test_unknown_usage_is_not_zero_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'1.json', Path(tmp)/'2.json'
            save(a, {'status': 'http_error'})
            save(b, {'status': 'received', 'response': {'usage': {'total_tokens': 100, 'cost': 0}}})
            result = trial.usage([a, b])['totals']
            self.assertEqual(result['known_total_tokens'], 100)
            self.assertEqual(result['unknown_total_tokens_attempts'], 1)
            self.assertEqual(result['unknown_reported_cost_usd_attempts'], 1)

    def test_exact_cdf_integral(self):
        self.assertAlmostEqual(cdf_loss([0, 1], [0, 1], .5), 1/12)
        self.assertAlmostEqual(cdf_loss([0, 1], [0, 1], 1), 1/3)
        self.assertAlmostEqual(cdf_loss([800, 2200], [0, 1], 1500), 1/12)

    def test_censored_quantile_summary_never_extrapolates_a_tail_value(self):
        from ForecastAgent.analysis.distributions import grid, range_metadata
        q = source_bundle('numeric')['request']; xs = grid(range_metadata(q))
        ps = [.6 + .38*i/(len(xs)-1) for i in range(len(xs))]
        summary = trial.forecast_summary({'payload': {'continuous_cdf': ps}}, q)
        self.assertIsNone(summary['p10']); self.assertIsNone(summary['median'])
        self.assertEqual(summary['quantile_censoring']['0.5']['bound'], xs[0])
        self.assertTrue(xs[0] <= summary['p90'] <= xs[-1])
        ps = [.02 + .38*i/(len(xs)-1) for i in range(len(xs))]
        summary = trial.forecast_summary({'payload': {'continuous_cdf': ps}}, q)
        self.assertIsNone(summary['median']); self.assertIsNone(summary['p90'])
        self.assertEqual(summary['quantile_censoring']['0.5']['bound'], xs[-1])


if __name__ == '__main__':
    unittest.main()
