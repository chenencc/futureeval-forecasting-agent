"""Full-queue partitioning, offline execution and idempotent campaign restore."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from ForecastAgent.supplement.campaign import run_campaign


class CampaignTests(unittest.TestCase):
    def fixture(self,path):
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('campaign.json',json.dumps({'tasks':{str(i):{'status':'closed_with_gaps'} for i in range(1,12)}}))
            for i in range(1,12):
                z.writestr(f'tasks/{i}/bundle.json',json.dumps({'pages':{},'fetch_attempts':[]}))

    def test_all_tasks_assessed_in_five_task_batches_and_not_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive=root/'parent.zip';self.fixture(archive)
            state=run_campaign(archive,root/'out',network=False)
            self.assertEqual([len(b['ids']) for b in state['batches']],[5,5,1])
            self.assertTrue(state['all_tasks_assessed'])
            with patch('ForecastAgent.supplement.campaign.run') as stage:
                run_campaign(archive,root/'out',network=False)
                stage.assert_not_called()

    def test_changed_campaign_network_policy_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive=root/'parent.zip';self.fixture(archive)
            run_campaign(archive,root/'out',network=False)
            with self.assertRaises(ValueError):run_campaign(archive,root/'out',network=True)


if __name__=='__main__':unittest.main()
