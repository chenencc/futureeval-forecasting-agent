"""Platform format gates, clipping constraints and typed replay acceptance."""
import copy
import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.distributions import (
    clip_categories, grid, payload, quantiles_to_cdf, range_metadata,
    standardize_cdf, validate_cdf,
)
from ForecastAgent.analysis.range_forecast import LEVELS, run, threshold_questions, parse_decision
from ForecastAgent.competition.nonbinary_debug import blind_input
from ForecastAgent.competition.queue import load, save


def metadata(kind='numeric', **flags):
    return {'id': '42', 'type': kind, 'scaling': {'range_min': 0., 'range_max': 100., 'zero_point': None},
        'inbound_outcome_count': 10 if kind == 'discrete' else 200,
        'open_lower_bound': False, 'open_upper_bound': False, **flags}


def platform_gate(case, question, cdf):
    """Check the external server contract separately from adapter validation."""
    n = question['inbound_outcome_count']
    case.assertEqual(len(cdf), n + 1)
    case.assertTrue(all(type(v) in (int, float) and 0 <= v <= 1 for v in cdf))
    for left, right in zip(cdf, cdf[1:]):
        delta = round(right - left, 9)
        case.assertGreaterEqual(delta, round(.01 / n, 9))
        case.assertLessEqual(delta, .2 * 200 / n + 1e-9)
    if question['open_lower_bound']:
        case.assertGreaterEqual(cdf[0], .001)
    else:
        case.assertEqual(cdf[0], 0)
    if question['open_upper_bound']:
        case.assertLessEqual(cdf[-1], .999)
    else:
        case.assertEqual(cdf[-1], 1)


class DistributionTests(unittest.TestCase):
    def test_multiclass_clipping_preserves_sum_and_handles_infeasible_policy(self):
        for n in (2, 3, 8, 24, 50):
            options = [str(i) for i in range(n)]
            raw = {o: float(i == 0) for i, o in enumerate(options)}
            adjusted = clip_categories(raw, options)
            self.assertAlmostEqual(sum(adjusted.values()), 1)
            self.assertTrue(all(.02 - 1e-10 <= v <= .98 + 1e-10 for v in adjusted.values()))
        with self.assertRaisesRegex(ValueError, 'infeasible'):
            clip_categories({str(i): 1 / 51 for i in range(51)}, [str(i) for i in range(51)])
        with self.assertRaises(ValueError):
            clip_categories({'a': float('nan'), 'b': .5}, ['a', 'b'])
        self.assertEqual(payload({'id': 42, 'type': 'binary'}, {'probability_yes': 1})['probability_yes'], .98)

    def test_peaky_cdf_obeys_every_boundary_combination_and_platform_caps(self):
        for lower in (False, True):
            for upper in (False, True):
                q = metadata(open_lower_bound=lower, open_upper_bound=upper)
                raw = [0.] * 100 + [1.] * 101
                cdf = standardize_cdf(raw, q)
                platform_gate(self, q, cdf)
                self.assertTrue(all(.02 - 1e-9 <= x <= .98 + 1e-9 for x in cdf[1:-1]))
                validate_cdf(cdf, q, clipped=True)
                self.assertEqual(cdf, standardize_cdf(cdf, q))

    def test_historical_discrete_grid_uses_bin_edges_and_real_counts(self):
        data = load(Path(__file__).parents[1] / 'fixtures/nonbinary-format-2026.json')
        for q in data['questions']:
            if q['type'] == 'multiple_choice':
                continue
            actual = grid(q)
            self.assertEqual(actual, q['scaling']['continuous_range'])
            cdf = standardize_cdf([i / (len(actual) - 1) for i in range(len(actual))], q)
            platform_gate(self, q, cdf)
            if q['type'] == 'discrete':
                self.assertNotEqual(q['inbound_outcome_count'], 200)

    def test_log_scaling_quantiles_and_date_timezone(self):
        q = metadata(scaling={'range_min': 1., 'range_max': 10000., 'zero_point': 0.})
        rows = [{'probability': p, 'value': 10000 ** p} for p in LEVELS]
        cdf = quantiles_to_cdf(rows, q, 0, 0)
        self.assertAlmostEqual(cdf[100], .5)
        platform_gate(self, q, standardize_cdf(cdf, q))
        q = metadata('date', scaling={'range_min': 1767225600., 'range_max': 1798761600., 'zero_point': None})
        rows = [{'probability': p, 'value': datetime.fromtimestamp(1767225600 + 31536000 * p, timezone.utc).isoformat()} for p in LEVELS]
        platform_gate(self, q, standardize_cdf(quantiles_to_cdf(rows, q, 0, 0), q))
        rows[0]['value'] = '2026-01-01T00:00:00'
        with self.assertRaisesRegex(ValueError, 'timezone'):
            quantiles_to_cdf(rows, q, 0, 0)

    def test_repeated_quantiles_and_closed_lower_mass(self):
        q = metadata('discrete')
        rows = [{'probability': p, 'value': 0. if p <= .75 else 80.} for p in LEVELS]
        raw = quantiles_to_cdf(rows, q, 0, 0)
        self.assertEqual(raw[0], 0)
        self.assertGreater(raw[1], .7)
        platform_gate(self, q, standardize_cdf(raw, q))

    def test_invalid_forecasts_fail_without_defaults(self):
        q = metadata()
        for raw in ([.5] * 201 + [.9], [float('nan')] * 201, [1.] * 100 + [0.] * 101):
            with self.assertRaises(ValueError):
                standardize_cdf(raw, q)
        with self.assertRaises(ValueError):
            range_metadata(metadata(open_upper_bound=None))
        post = {'id': 1, 'question': {**q, 'title': 'Quantity', 'resolution': '100',
            'aggregations': {'value': 99}, 'resolution_criteria': 'Use the official value'}}
        blind = blind_input(post, 42)
        self.assertNotIn('resolution', blind)
        self.assertNotIn('aggregations', blind)
        self.assertEqual(blind['scaling'], q['scaling'])

    def test_many_valid_distributions(self):
        rng = random.Random(42)
        for n in (2, 10, 91, 200):
            for _ in range(12):
                q = metadata('discrete', inbound_outcome_count=n, open_lower_bound=True, open_upper_bound=True)
                raw = sorted(rng.random() for _ in range(n + 1))
                platform_gate(self, q, standardize_cdf(raw, q))

    def test_discrete_spike_remains_valid_after_server_rounding(self):
        for n in (31, 91, 151, 200):
            q = metadata('discrete', inbound_outcome_count=n)
            raw = [0.] * (n // 2) + [1.] * (n + 1 - n // 2)
            cdf = standardize_cdf(raw, q)
            platform_gate(self, q, cdf)
            for a, b in zip(cdf, cdf[1:]):
                self.assertLessEqual(round(round(b, 10) - round(a, 10), 9), 40 / n)


class RangeExecutionTests(unittest.TestCase):
    def test_real_execution_contract_replay_and_partial_decision(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            'OPENROUTER_API_KEY': 'offline-test', 'FORECAST_MODEL': 'nvidia/nemotron-3-ultra-550b-a55b:free'}):
            root = Path(temp)
            request = {**metadata(), 'question_type': 'numeric', 'question': 'A quantity?',
                'resolution_criteria': 'Use the official measured quantity', 'mode': 'live'}
            save(root / 'bundle.json', {'request': request,
                'pages': {'https://example.org': {'content': 'The measurement uses units.'}}})
            report = {'rule_decomposition': 'Measure the quantity', 'base_rate': 'Unknown',
                'distribution_rationale': 'Offline format fixture', 'contradictions': 'None',
                'source_independence': 'One fixture', 'facts': [{'source_id': 'S1',
                'quote': 'The measurement uses units.', 'claim': 'Unit definition'}], 'gaps': ['No live evidence'],
                'quantiles': [{'probability': p, 'value': 100 * p} for p in LEVELS],
                'below_lower_bound': 0., 'above_upper_bound': 0.}
            def ask(messages, key, **kwargs):
                record = {'request': {'model': kwargs['model_route'].model()}, 'status': 'reserved'}
                token = kwargs['observer']('reserve', record)
                message = {'tool_calls': [{'function': {'name': 'record_range', 'arguments': json.dumps(report)}}]}
                record.update(status='received', response={'choices': [{'message': message}]})
                kwargs['observer']('complete', record, token)
                return message
            def decision(state, questions, key, observer):
                self.assertNotIn('quantiles', state['analysis'])
                self.assertNotIn('below_lower_bound', state['analysis'])
                token = observer('reserve', {'request': {'model': 'inception/mercury-decide:free'}, 'status': 'reserved'})
                response = {'model': 'inception/mercury-decide:free', 'answers': {k: {'type': 'noul', 'noul': int(k.split('_')[1]) / 200} for k in questions}}
                observer('complete', {'request': {'model': 'inception/mercury-decide:free'}, 'status': 'received', 'response': response}, token)
                return response
            output = root / 'out'
            result = run(root / 'bundle.json', output, ask=ask, decision=decision)
            self.assertEqual(result['status'], 'completed')
            platform_gate(self, request, result['payload_preview']['continuous_cdf'])
            def forbidden(*args, **kwargs):
                raise AssertionError('Replay called provider')
            (output / 'analysis.json').unlink()
            (output / 'decision-response.json').unlink()
            self.assertEqual(result, run(root / 'bundle.json', output, ask=forbidden, decision=forbidden))
            def failed(*args, **kwargs):
                raise RuntimeError('Decision unavailable')
            partial = run(root / 'bundle.json', root / 'partial', ask=ask, decision=failed)
            self.assertEqual(partial['status'], 'partial')
            self.assertIsNone(partial['mercury_cdf'])
            self.assertEqual(partial['selection'], 'single_available_reasoning_route')
            questions = threshold_questions(range_metadata(request))
            bad = {'answers': {k: {'noul': 1 - int(k.split('_')[1]) / 200} for k in questions}}
            with self.assertRaisesRegex(ValueError, 'coherent'):
                parse_decision(bad, range_metadata(request), questions)


if __name__ == '__main__':
    unittest.main()
