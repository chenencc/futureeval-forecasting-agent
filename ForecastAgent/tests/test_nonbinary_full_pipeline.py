"""Test seed preservation without spending provider credits."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import nonbinary_full_pipeline as pipeline
from ForecastAgent.analysis.pilot import load, save


class BridgeTests(unittest.TestCase):
    def test_lifetime_budget_and_identity(self):
        row=load(pipeline.mercury.INPUT)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); seed=root/'seed'; target=root/'target'
            save(seed/'bundle.json',{'request':pipeline.mercury.request(row)})
            save(seed/'search-http/001.json',{'query':'gas price','status':'received'})
            save(seed/'search.json',{'results':[]})
            save(seed/'captures/one.json',{'url':'https://www.eia.gov/test','status':'failed','error':'HTTP Error 403'})
            with patch.dict('os.environ',{'EXA_API_KEY':'test'}):
                pipeline.bridge(row,seed,target)
            bundle=load(target/'bundle.json')
            self.assertEqual(bundle['acquisition_limits'],{'tavily_basic':3,'exa_search':1})
            self.assertEqual(len(bundle['searches']),1)
            self.assertEqual(len(bundle['fetch_attempts']),1)
            self.assertEqual(bundle['fetch_attempts'][0]['status'],'failed')
            pipeline.bridge(row,seed,target)
            self.assertEqual(bundle,load(target/'bundle.json'))
            save(seed/'search.json',{'results':[],'changed':True})
            with self.assertRaisesRegex(ValueError,'Seed identity changed'):
                pipeline.bridge(row,seed,target)


if __name__=='__main__':unittest.main()
