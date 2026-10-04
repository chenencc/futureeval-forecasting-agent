"""Frozen corpus identity, held-out isolation and pairing invariants."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.experiments import material_benchmark as benchmark,solid_collection


class BenchmarkTests(unittest.TestCase):
    def test_resume_retains_failed_arm_and_writes_both_masked_results(self):
        manifest=benchmark.read(benchmark.HERE.with_name('MATERIAL_BENCHMARK10.json'))
        selected=manifest['cases'][0]
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            benchmark.write(out/'shared-input.json',{'payload':{'question':{},'passages':[]}})
            failure={'status':'failed','error':'source_batch_too_large','needs':[]}
            benchmark.write(out/'private/baseline/result.json',failure)
            original=(out/'private/baseline/result.json').read_bytes()
            def fake_invoke(repo,mode,input_path,result_path):
                self.assertEqual(result_path.parent.name,'candidate')
                benchmark.write(result_path,{'status':'completed','needs':[]})
            with patch.object(benchmark,'invoke',side_effect=fake_invoke) as calls:
                benchmark.complete_pair(selected,manifest,Path('baseline'),out,resume=True)
                self.assertEqual(calls.call_count,1)
            self.assertEqual((out/'private/baseline/result.json').read_bytes(),original)
            self.assertTrue((out/'blind/R1.json').exists())
            self.assertTrue((out/'blind/R2.json').exists())
            with patch.object(benchmark,'invoke') as calls:
                benchmark.complete_pair(selected,manifest,Path('baseline'),out,resume=True)
                calls.assert_not_called()

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
