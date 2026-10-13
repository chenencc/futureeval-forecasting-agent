"""No backdated forecasts, stale graph relabeling or late-price substitution."""
import copy
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.market_repricing.common import read, write
from ForecastAgent.market_repricing.material_revisions import evaluate, forecast_eligibility, freeze, publish


class MaterialRevisions(unittest.TestCase):
    def task(self):
        return {'key': 'polymarket:1', 'market': {'created_at': '2026-10-12T10:00:00Z'},
            'status': 'completed', 'forecast': {'probability': .2}, 'executions': 2}

    def test_later_material_and_old_prediction_remain_separate(self):
        task = self.task()
        bundle = {'request': {'as_of_utc': '2026-10-12T11:00:00Z'}, 'pages': {},
            'research_loop': {'current': {'nodes': []}}}
        before = copy.deepcopy(bundle)
        child, meta = freeze(bundle, task, '2026-10-13T09:00:00Z', [{'sha256': 'parent'}],
            [{'url': 'https://example.org/new', 'content': 'new', 'retrieved_at_utc': '2026-10-13T08:00:00Z'}])
        self.assertEqual(bundle, before)
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(meta['price_schedule']['targets']['24'], '2026-10-13T10:00:00+00:00')
        self.assertFalse(meta['historical_cutoff_eligible'])
        self.assertNotEqual(child['request']['as_of_utc'], before['request']['as_of_utc'])

    def test_no_future_page_or_silent_overwrite(self):
        bundle = {'request': {}, 'pages': {'https://example.org/new': {'url': 'https://example.org/new', 'content': 'old'}}}
        with self.assertRaises(ValueError): freeze(bundle, self.task(), '2026-10-13T09:00:00Z', [],
            [{'url': 'https://example.org/new', 'content': 'new'}])
        with self.assertRaises(ValueError): freeze({'request': {}, 'pages': {}}, self.task(), '2026-10-13T09:00:00Z', [],
            [{'url': 'https://example.org/new', 'retrieved_at_utc': '2026-10-13T11:00:00Z'}])

    def forecast(self):
        return {'price_schedule': {'origin_role': 'market_created_at', 'targets': {'24': '2026-10-13T10:00:00Z'}},
            'sealed_at': '2026-10-13T09:00:00Z', 'timely': True, 'baseline': {'24': .5},
            'arms': {'original': {'status': 'completed', 'price_means': {'24': .4}},
                     'mapped': {'status': 'completed', 'price_means': {'24': .45}}}}

    def label(self):
        return {'target_at': '2026-10-13T10:00:00Z', 'origin_role': 'market_created_at', 'status': 'quoted',
            'label': .4, 'window_minutes': 30, 'quote': {'received_at': '2026-10-13T09:50:00Z'}}

    def test_backdated_or_unmatched_forecasts_never_score(self):
        self.assertEqual(forecast_eligibility(self.forecast(), 24, self.label()), 'eligible')
        self.assertEqual(forecast_eligibility(self.forecast() | {'sealed_at': '2026-10-13T10:00:00Z'}, 24, self.label()), 'forecast_after_target')
        self.assertEqual(forecast_eligibility(self.forecast(), 24, self.label() | {'target_at': '2026-10-14T10:00:00Z'}), 'label_target_mismatch')
        self.assertEqual(forecast_eligibility(self.forecast(), 24, self.label() | {'origin_role': 'forecast_anchor_at'}), 'label_origin_mismatch')
        for clock in ('2026-10-13T10:01:00Z', '2026-10-13T09:29:00Z'):
            self.assertNotEqual(forecast_eligibility(self.forecast(), 24, self.label() | {'quote': {'received_at': clock}}), 'eligible')

    def test_index_and_metrics_keep_exact_material_map_forecast_binding(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'case'
            result = {'key': 'polymarket:1', 'revision_id': 'r1', 'family': 'event1',
                'material_sha256': 'material', 'graph_sha256': 'graph', 'map_status': 'updated',
                'price_schedule': self.forecast()['price_schedule'], 'forecast': self.forecast()}
            write(path / 'revision-result.json', result)
            index = publish(root, [path])
            value = evaluate(index, {'tasks': {'polymarket:1': {'prices': {'24': self.label()}}}})
            self.assertEqual(value['summary']['24']['paired_metrics']['original']['mae'], 0)
            self.assertEqual(value['summary']['24']['paired_metrics']['mapped']['paired_n'], 1)
            self.assertEqual(read(Path(root) / 'revision-index.json')['cases'][0]['graph_sha256'], 'graph')
            write(path / 'revision-result.json', result | {'graph_sha256': 'modified'})
            with self.assertRaises(ValueError): evaluate(index, {})


if __name__ == '__main__':
    unittest.main()
