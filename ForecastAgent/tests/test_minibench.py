"""MiniBench routing, isolation, typed delivery and replay contracts."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.competition import live
from ForecastAgent.competition.queue import load, save
from ForecastAgent.competition.tournaments import configured_tournament
from ForecastAgent.releases import stress, v1_0_3 as release


class MiniBenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'FORECAST_TOURNAMENT':'minibench','OPENROUTER_API_KEY':'offline'})
        self.env.start();self.addCleanup(self.env.stop);self.addCleanup(self.tmp.cleanup)
        binding=patch.object(stress,'release',release);binding.start();self.addCleanup(binding.stop)

    def incoming(self, count=7):
        docs={str(100+i):{'id':100+i,'title':str(i),'user_permission':'forecaster',
              'question':stress.question(i,stress.KINDS[(i-1)%5])} for i in range(1,count+1)}
        folder=stress.snapshot(self.root/'incoming',docs)
        index=load(folder/'index.json');index['tournament']='minibench';save(folder/'index.json',index)
        return folder,stress.Platform(docs)

    def test_five_then_two_all_types_and_replay_no_duplicate(self):
        incoming,client=self.incoming();worker=self.root/'worker'
        with patch('ForecastAgent.providers.decisions.decide',stress.decide):
            first=release.once(worker,incoming,enabled=True,client=client,collector=stress.saved_collector,limit=5)
            self.assertEqual(len(client.writes),5)
            self.assertEqual(first['state_distribution'],{'accepted':5,'queued':2})
            second=release.once(worker,incoming,enabled=True,client=client,collector=stress.saved_collector,limit=5)
            self.assertEqual(second['state_distribution'],{'accepted':7})
            release.once(worker,incoming,enabled=True,client=client,collector=lambda *a:self.fail('Repeat collection'))
        self.assertEqual(len(client.writes),7)
        self.assertTrue(all(c['is_private'] for c in client.private))
        self.assertEqual(load(worker/'campaign.json')['tournament'],'minibench')

    def test_fall_checkpoint_rejected_and_not_changed(self):
        incoming,_=self.incoming(1);worker=self.root/'worker'
        save(worker/'campaign.json',{'schema':live.SCHEMA,'tournament':'fall-futureeval-2026','tasks':{}})
        before=(worker/'campaign.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'Wrong preserved campaign'):
            release.seed(worker,incoming)
        self.assertEqual((worker/'campaign.json').read_bytes(),before)

    def test_wrong_snapshot_rejected(self):
        incoming,_=self.incoming(1);index=load(incoming/'index.json')
        index['tournament']='fall-futureeval-2026';save(incoming/'index.json',index)
        with self.assertRaisesRegex(ValueError,'snapshot'):
            release.supervise(self.root/'worker',incoming)

    def test_verification_does_not_start_models_or_submission(self):
        incoming,_=self.incoming(2)
        result=release.supervise(self.root/'worker',incoming,submit=False,
             process_runner=lambda *a,**k:self.fail('Verification started model child'))
        self.assertEqual(result['state_distribution'],{'queued':2})
        self.assertFalse(result['submission_enabled'])

    def test_unknown_tournament_rejected(self):
        with patch.dict(os.environ,{'FORECAST_TOURNAMENT':'other'}):
            with self.assertRaises(ValueError):configured_tournament()

    def test_default_fall_identity_unchanged(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(configured_tournament(),'fall-futureeval-2026')


if __name__=='__main__':unittest.main()
