"""Do not fill missing windows, mix resolutions or reward later predictions."""
import copy
import unittest
from ForecastAgent.market_repricing.official_map_metrics import evaluate,ARMS


class OfficialMetricsTests(unittest.TestCase):
    def data(self):
        case={'key':'p:1','family':'f:1','anchor':{'midpoint':.5},
            'price_schedule':{'origin_role':'market_created_at','targets':{'24':'2026-10-14T00:00:00Z'}},
            'arms':{a:{'status':'completed','sealed_at':'2026-10-13T22:00:00Z','price_means':{'24':.4},
                'eligible_for_model_metrics':True,'eventual_probability_yes':.4} for a in ARMS}}
        label={'status':'quoted','target_at':'2026-10-14T00:00:00Z','origin_role':'market_created_at',
            'label':.45,'window_minutes':30,'quote':{'received_at':'2026-10-13T23:50:00Z'}}
        tracking={'tasks':{'p:1':{'prices':{'24':label},'final':{'status':'resolved','result':'yes',
            'eligible_for_event_brier':True,'resolved_at':'2026-10-15T00:00:00Z'}}}}
        return {'cases':[case]},tracking

    def test_paired_three_arm_price_metrics_and_distinct_brier(self):
        report,tracking=self.data();r=evaluate(report,tracking)
        self.assertEqual(r['summary']['24']['paired_metrics']['official_map']['paired_n'],1)
        self.assertAlmostEqual(r['summary']['24']['paired_metrics']['official_map']['mae'],.05)
        self.assertAlmostEqual(r['event_metrics']['official_map']['brier'],.36)

    def test_shallow_missing_or_late_quotes_cannot_complete_pair(self):
        report,tracking=self.data();report['cases'][0]['arms']['official_map']['eligible_for_model_metrics']=False
        self.assertEqual(evaluate(report,tracking)['summary']['24']['paired_metrics']['original']['paired_n'],0)
        report,tracking=self.data();tracking['tasks']['p:1']['prices']['24']['quote']['received_at']='2026-10-14T00:01:00Z'
        self.assertEqual(evaluate(report,tracking)['summary']['24']['paired_metrics']['original']['paired_n'],0)

    def test_no_brier_without_verified_resolution_time(self):
        report,tracking=self.data();tracking['tasks']['p:1']['final'].pop('resolved_at')
        self.assertEqual(evaluate(report,tracking)['event_metrics']['official_map']['paired_n'],0)
