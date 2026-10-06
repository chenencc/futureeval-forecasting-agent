"""Production promotion contracts using the new immutable entrypoint."""
import copy
from unittest.mock import patch
from ForecastAgent.tests import test_release_1_0_2 as previous
from ForecastAgent.releases import v1_0_3 as release, stress
from ForecastAgent.competition.queue import load, save, digest


class ProductionReleaseTests(previous.ReleaseTests):
    def setUp(self):
        super().setUp()
        for target in (previous, stress):
            binding=patch.object(target,'release',release)
            binding.start();self.addCleanup(binding.stop)

    def test_prior_accepted_receipt_is_unchanged_and_no_child_is_started(self):
        incoming,_=self.args(self.docs(1));worker=self.root/'worker'
        release.seed(worker,incoming)
        state=load(worker/'campaign.json');task=state['tasks']['1']
        task.update(stage='accepted',release_version='1.0.1',collection_executions=1)
        before=copy.deepcopy(task);save(worker/'campaign.json',state)
        receipt=worker/'tasks/1/submission.json';save(receipt,{'status':'accepted','forecast':.18})
        receipt_hash=release.file_hash(receipt)
        def forbidden(*args,**kwargs):raise AssertionError('Terminal task started a child')
        report=release.supervise(worker,incoming,submit=True,process_runner=forbidden)
        self.assertEqual(report['state_distribution'],{'accepted':1})
        self.assertEqual(report['worker_attempts'],[])
        self.assertEqual(load(worker/'campaign.json')['tasks']['1'],before)
        self.assertEqual(release.file_hash(receipt),receipt_hash)

    def test_prior_inflight_budget_is_blocked_without_reset(self):
        incoming,_=self.args(self.docs(1));worker=self.root/'worker'
        release.seed(worker,incoming)
        state=load(worker/'campaign.json');task=state['tasks']['1']
        task.update(stage='retry_wait',release_version='1.0.1',collection_executions=2)
        save(worker/'campaign.json',state)
        budget=worker/'tasks/1/retrieval/bundle.json';save(budget,{'used_searches':3})
        before=release.file_hash(budget)
        report=release.supervise(worker,incoming,submit=True,process_runner=lambda *a,**k: self.fail('Budget restarted'))
        after=load(worker/'campaign.json')['tasks']['1']
        self.assertEqual(after['stage'],'blocked_integrity')
        self.assertEqual(after['collection_executions'],2)
        self.assertEqual(release.file_hash(budget),before)
        self.assertEqual(report['worker_attempts'],[])

    def test_direct_old_native_acquisition_is_rejected_before_provider(self):
        folder=self.root/'retrieval';save(folder/'release-1.0.2/collection/bundle.json',{'used_searches':3})
        with patch.object(release.pipeline,'run',side_effect=AssertionError('Provider called')):
            with self.assertRaisesRegex(ValueError,'Previous acquisition'):
                release.collect({'id':'1'},folder)
