"""Third-arm comparisons retain late/missing arms without invalidating controls."""
import copy
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.market_repricing.common import digest, policy, read, state, write
from ForecastAgent.market_repricing.evaluate import run
from ForecastAgent.market_repricing.tracking import creation_schedule
from ForecastAgent.market_repricing.worker import forecast_policy


class ProductionEvaluation(unittest.TestCase):
    def test_per_arm_seals_and_pair_attrition_are_separate(self):
        market = {'family': 'family', 'created_at': '2026-10-12T00:00:00Z', 'rules_sha256': 'rules'}
        schedule = creation_schedule({'market': market})
        def arm(seal, timely=True):
            return {'status': 'completed', 'price_means': {'24': .4}, 'eventual_probability_yes': .4,
                    'sealed_at': seal, 'timely': timely}
        forecast = {'timely': True, 'sealed_at': '2026-10-12T01:00:00Z', 'price_schedule': schedule,
                    'baseline': {'24': .45, '72': .45, '168': .45}, 'pair_audit': {'map_delivered': True},
                    'arms': {'original': arm('2026-10-12T01:00:00Z'),
                             'mapped': arm('2026-10-12T01:01:00Z'),
                             'official_map': arm('2026-10-13T00:01:00Z')}}
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); monitor = root/'monitor'; worker = root/'worker'; cfg = policy()
            w = state(worker, 'worker', cfg); m = state(monitor, 'monitor', cfg)
            w['tasks'] = {'new': {'market': market, 'forecast': forecast, 'status': 'completed'}}
            m['markets'] = {'new': market}; write(worker/'state.json', w); write(monitor/'state.json', m)
            write(monitor/'labels'/ (digest('new')+'.json'), {'24': {'status': 'quoted', 'target_at': schedule['targets']['24'], 'label': .5}})
            result = run(monitor, worker, root/'metrics.json')
            row = next(r for r in result['rows'] if r['horizon_hours'] == 24)
            self.assertEqual(set(row['arms']), {'original', 'mapped'})
            comparison = result['summary']['market_created_at:24']['comparisons']
            self.assertEqual(comparison['original_vs_mapped']['metrics']['original']['paired_n'], 1)
            self.assertEqual(comparison['three_arm']['metrics']['original']['paired_n'], 0)
            forecast['arms']['official_map'] = arm('2026-10-12T01:02:00Z', timely=False)
            write(worker/'state.json', w)
            self.assertNotIn('official_map', next(r for r in run(monitor, worker, root/'metrics.json')['rows'] if r['horizon_hours']==24)['arms'])
            forecast['arms']['official_map'] = arm('2026-10-12T01:02:00Z')
            write(worker/'state.json', w)
            comparison = run(monitor, worker, root/'metrics.json')['summary']['market_created_at:24']['comparisons']
            self.assertEqual(comparison['three_arm']['metrics']['official_map']['paired_n'], 1)

    def test_analysis_routing_does_not_mutate_shared_caps(self):
        cfg = policy(); before = copy.deepcopy(cfg)
        routed = forecast_policy({'analysis_arms': ['original', 'mapped', 'official_map']}, cfg)
        self.assertEqual(cfg, before)
        for key, value in cfg.items():
            if key not in ('analysis_arms', 'horizons_hours', 'price_target_origin'):
                self.assertEqual(routed[key], value)


if __name__ == '__main__': unittest.main()
