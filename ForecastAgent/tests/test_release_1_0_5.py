"""Collection-only promotion, terminal receipt preservation and mixed surfaces."""
import copy
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.releases import v1_0_5 as release, stress
from ForecastAgent.tests import test_release_1_0_2 as base, test_release_1_0_3 as prior
from ForecastAgent.tests import test_release_1_0_4 as previous
from ForecastAgent.competition.queue import load, save


class ProductionReleaseTests(previous.ProductionReleaseTests):
    def setUp(self):
        base.ReleaseTests.setUp(self)
        for target in (base,prior,previous,stress):
            binding=patch.object(target,'release',release)
            binding.start();self.addCleanup(binding.stop)

    def test_empty_nonresumable_material_blocks_once_without_inference_or_delivery(self):
        incoming,client=self.args(self.docs(1));calls=[]
        def empty(request,folder):
            calls.append(request['id'])
            result={'request':request,'result':{'status':'material_unavailable',
                    'incomplete':True,'resumable':False,'termination_reason':'supplement_without_material'}}
            save(folder/'bundle.json',result)
            return result
        with patch.object(release,'collect',side_effect=empty), \
             patch.object(release,'analyze',side_effect=AssertionError('Empty package analyzed')):
            first=release.once(self.root/'worker',incoming,enabled=True,client=client)
            second=release.once(self.root/'worker',incoming,enabled=True,client=client)
        self.assertEqual(first['state_distribution'],{'provider_blocked':1})
        self.assertEqual(second['state_distribution'],{'provider_blocked':1})
        self.assertEqual(calls,['1']);self.assertEqual(client.writes,[])
        task=load(self.root/'worker/campaign.json')['tasks']['1']
        self.assertEqual(task['collection_executions'],1)
        self.assertIn('without readable bodies',task['last_error'])


class ReleaseIdentityTests(TestCase):
    def test_release_manifest_and_unchanged_analysis_identity(self):
        manifest=release.verify_release()
        self.assertEqual(manifest['version'],'1.0.5')
        self.assertFalse(manifest.get('development_only',False))
        self.assertEqual(manifest['analysis_core_tag'],'v1.0.1')
        release.pipeline.verify_baseline()
        self.assertEqual(release.context_decisions.VERSION,'1.0.4')

    def test_analysis_prompts_are_identical_to_prior_source(self):
        import subprocess
        from pathlib import Path
        root=release.pipeline.ROOT
        for name in ('ForecastAgent/releases/context_decisions.py',
                     'ForecastAgent/analysis/mercury_evidence_chain.py',
                     'ForecastAgent/acquisition/context_delivery.py'):
            original=subprocess.check_output(['git','show','478def7:'+name],cwd=root)
            self.assertEqual((Path(root)/name).read_bytes().replace(b'\r\n',b'\n'),
                             original.replace(b'\r\n',b'\n'))
