"""End-to-end bounded handoff and review recovery without provider calls."""
import tempfile
import unittest
from pathlib import Path
from ForecastAgent.analysis.pilot import load
from ForecastAgent.tests.test_material_gap_workflow import bundle
from ForecastAgent.supplement import enhanced, material_review, witness_contract
from ForecastAgent.supplement.delivery import build


class DeliveryTests(unittest.TestCase):
    def test_new_cohort_is_frozen_outside_repair_and_has_no_outcome_inputs(self):
        from ForecastAgent.experiments import solid_collection
        manifest=load(Path(solid_collection.__file__).parents[1]/'fixtures/material_generalization5.json')
        self.assertEqual(solid_collection.COHORTS['generalization5'],[c['id'] for c in manifest['cases']])
        for c in manifest['cases']:
            self.assertNotIn(c['id'],manifest['excluded_repair_ids'])
            self.assertTrue(c['required_materials'])
            request=solid_collection.question(c['id'],'frozen-test','solid_v2')
            self.assertTrue({'resolution','resolved_to','probability','as_of_utc'}.isdisjoint(request))

    def test_review_failure_exports_every_need_and_resume_does_not_repeat(self):
        calls=[]
        def failed(payload, state, folder):
            calls.append(payload)
            self.assertEqual(payload['coverage_protocol'],witness_contract.PROTOCOL)
            self.assertTrue(all('witness_contract' in n for n in payload['needs']))
            raise material_review.ReviewError('output_truncated')
        b=bundle()
        with tempfile.TemporaryDirectory() as folder:
            result=enhanced.run(b,folder,network=True,material_agent=failed)
            result2=enhanced.run(b,folder,network=True,material_agent=failed)
            saved=load(Path(folder)/'material-delivery.json')
        self.assertEqual(len(calls),1)
        self.assertEqual(saved,result2['material_delivery'])
        self.assertEqual(len(saved['needs']),len(result['material_need_ledger']['needs']))
        self.assertEqual(saved['state'],'completed_with_recorded_gaps')
        self.assertTrue(all(n['status']=='review_failed' for n in saved['needs']))
        self.assertIn('output_truncated',saved['review_errors'])

    def test_partial_success_is_not_downgraded_by_independent_failure(self):
        needs=[{'id':'ok','condition':'Event record','target_material_captured':True},
               {'id':'gap','condition':'Release date','target_material_captured':False}]
        result=build({'needs':needs,'review_errors':['invalid_review_shape']},{})
        self.assertEqual([n['status'] for n in result['needs']],['material_captured','review_failed'])
        self.assertEqual(result['state'],'completed_with_recorded_gaps')
        self.assertFalse(result['automatic_resubmit'])
        self.assertFalse(result['semantic_completeness_verified'])
