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
    def test_missing_initial_price_keeps_event_brier_and_unpaired_price_errors(self):
        market = {'family': 'family', 'created_at': '2026-10-12T00:00:00Z', 'rules_sha256': 'rules',
            'final_observation': {'status': 'resolved', 'result': 'yes', 'observed_at': '2026-10-22T00:00:00Z'}}
        timing = creation_schedule({'market': market})
        forecast = {'timely': True, 'sealed_at': '2026-10-12T01:00:00Z', 'price_schedule': timing,
            'baseline': {'24': None, '72': None, '168': None}, 'arms': {'original': {
                'status': 'completed', 'price_means': {'24': .4}, 'eventual_probability_yes': .4}}}
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); monitor = root/'monitor'; worker = root/'worker'
            m, w = state(monitor, 'monitor', policy()), state(worker, 'worker', policy())
            m['markets'] = {'new': market}
            w['tasks'] = {'new': {'market': market, 'status': 'completed', 'forecast': forecast}}
            write(monitor/'state.json', m); write(worker/'state.json', w)
            write(monitor/'labels'/(digest('new')+'.json'), {'24': {'status': 'quoted',
                'target_at': timing['targets']['24'], 'label': .5}})
            result = run(monitor, worker, root/'evaluation.json')
            row = next(r for r in result['rows'] if r['horizon_hours'] == 24)
            self.assertIsNone(row['baseline'])
            self.assertAlmostEqual(row['unpaired_arms']['original']['error'], -.1)
            self.assertIsNone(row['unpaired_arms']['original']['direction_correct'])
            self.assertEqual(result['event_brier']['original']['n'], 1)
            self.assertAlmostEqual(result['event_brier']['original']['brier'], .36)
            self.assertEqual(result['summary']['market_created_at:24']['comparisons']['original_vs_mapped']['metrics']['no_change']['paired_n'], 0)

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

    def test_worker_first_approved_migration_evaluates_without_rewriting_sources_or_labels(self):
        market = {'family':'family','created_at':'2026-10-12T00:00:00Z','rules_sha256':'rules'}
        schedule = creation_schedule({'market':market})
        cfg = policy(); old = dict(cfg, maximum_new_tasks_per_day=30, maximum_worker_model_attempts_per_utc_day=400); old.pop('enrollment_window_hours')
        forecast = {'timely':True,'sealed_at':'2026-10-12T01:00:00Z','price_schedule':schedule,
            'baseline':{'24':.45,'72':.45,'168':.45},'pair_audit':{'map_delivered':True},
            'arms':{name:{'status':'completed','price_means':{'24':value},'eventual_probability_yes':value,
                'timely':True,'sealed_at':'2026-10-12T01:00:00Z'} for name,value in [('original',.4),('mapped',.6)]}}
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); monitor=root/'monitor'; worker=root/'worker'
            m=state(monitor,'monitor',old); w=state(worker,'worker',cfg)
            m['markets']={'new':market}; w['tasks']={'new':{'market':market,'forecast':forecast,'status':'completed'}}
            write(monitor/'state.json',m); write(worker/'state.json',w)
            label_path=monitor/'labels'/(digest('new')+'.json')
            write(label_path,{'24':{'status':'quoted','target_at':schedule['targets']['24'],'label':.5}})
            before=(monitor/'state.json').read_bytes(); label_bytes=label_path.read_bytes()
            result=run(monitor,worker,root/'metrics.json')
            self.assertEqual((monitor/'state.json').read_bytes(),before)
            self.assertEqual(label_path.read_bytes(),label_bytes)
            row=next(r for r in result['rows'] if r['horizon_hours']==24)
            self.assertAlmostEqual(row['arms']['original']['error'],-.1)
            self.assertAlmostEqual(row['arms']['mapped']['error'],.1)
            write(monitor/'state.json',state(monitor,'monitor',cfg))
            self.assertEqual(run(monitor,worker,root/'reference.json'),result)

    def test_evaluation_rejects_unapproved_policy_or_quota_changes(self):
        cfg=policy(); old=dict(cfg, maximum_new_tasks_per_day=30, maximum_worker_model_attempts_per_utc_day=400); old.pop('enrollment_window_hours')
        for change in ({'maximum_worker_model_attempts_per_task':61},{'maximum_new_tasks_per_day':31}):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                root=Path(root); monitor=root/'monitor'; worker=root/'worker'
                write(monitor/'state.json',state(monitor,'monitor',old | change))
                write(worker/'state.json',state(worker,'worker',cfg))
                with self.assertRaises(ValueError):run(monitor,worker,root/'metrics.json')


if __name__ == '__main__': unittest.main()
