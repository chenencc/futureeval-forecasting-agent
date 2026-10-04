"""Frozen corpus identity, held-out isolation and pairing invariants."""
import unittest
from ForecastAgent.experiments import material_benchmark as benchmark,solid_collection


class BenchmarkTests(unittest.TestCase):
    def test_five_regression_fixtures_are_hash_verified_and_labeled(self):
        manifest=benchmark.read(benchmark.HERE.with_name('MATERIAL_REGRESSION5.json'))
        self.assertEqual(len(manifest['cases']),5)
        items=[benchmark.fixture(c) for c in manifest['cases']]
        labels=[r for x in items for r in x['labels']]
        self.assertTrue(any(x['expected_eligible'] for x in labels))
        self.assertTrue(any(not x['expected_eligible'] for x in labels))
        self.assertEqual(len(labels),12)

    def test_ten_cases_exclude_every_repair_cohort_and_freeze_formats(self):
        manifest=benchmark.read(benchmark.HERE.with_name('MATERIAL_BENCHMARK10.json'))
        ids={c['id'] for c in manifest['cases']}
        self.assertEqual(len(ids),10)
        for name in ['pilot5','material_new5','new15','material_validation5']:
            self.assertFalse(ids & set(solid_collection.COHORTS[name]))
        self.assertEqual(ids,set(solid_collection.COHORTS['benchmark10']))
        formats={f for c in manifest['cases'] for f in c['formats']}
        self.assertTrue({'html','pdf','json','table'} <= formats)
        self.assertTrue(all(len(c['required_materials'])==2 for c in manifest['cases']))
        self.assertFalse(manifest['model_fallback'])
        self.assertEqual(manifest['assessment_per_arm']['max_http_attempts'],3)


if __name__=='__main__':unittest.main()
