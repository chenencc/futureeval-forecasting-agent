"""An interrupted raw export preserves native evidence and every reservation."""
import copy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import full_chain_ten_raw_handoff as trial
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.tests.test_full_chain_ten_resume import censored


def interrupted():
    b = censored()
    b['result'].update(status='partial', termination_reason='context_projection_failure',
                       execution_report={'owner':'program','interrupted':True,'pending_passage_count':16})
    return b


class RawHandoffTests(TestCase):
    def test_only_local_projection_and_readable_received_material_qualifies(self):
        b = interrupted(); self.assertTrue(trial.eligible(b))
        for reason in ('shared_account_quota','model_provider_error','transport_failure'):
            bad = copy.deepcopy(b); bad['result']['termination_reason'] = reason
            self.assertFalse(trial.eligible(bad))
        b['model_attempts'][0] = {'status':'failed'}
        self.assertFalse(trial.eligible(b))
        b = interrupted(); b['pages'] = {}; self.assertFalse(trial.eligible(b))
        b = interrupted(); b['result']['execution_report']['owner'] = 'model'
        self.assertFalse(trial.eligible(b))

    def test_bridge_preserves_partial_status_and_all_native_files_without_calls(self):
        _, c = trial.original.protocol(); request = c['requests'][0]; qid = request['id']
        with TemporaryDirectory() as tmp:
            output = Path(tmp); folder = output/qid/'acquisition'; root = folder/'collection'
            frozen = trial.original.pipeline.identity(request, True)
            save(folder/'identity.json', frozen)
            save(folder/'state.json', {'stage':'collection','identity_sha256':digest(frozen),'interrupted':True})
            b = interrupted(); b['request'] = frozen['request']
            journal = root/'model_calls/0001.json'; save(journal, {'status':'received','response':{'usage':{'total_tokens':42}}})
            b['model_attempts'] = [{'status':'received','path':'model_calls/0001.json',
                                    'sha256':hashlib.sha256(journal.read_bytes()).hexdigest()}]
            save(root/'bundle.json', b); before = trial.manifest(root)
            with patch('ForecastAgent.agent.run_research') as collector:
                self.assertTrue(trial.bridge(output,qid)); self.assertFalse(trial.bridge(output,qid))
                collector.assert_not_called()
            self.assertEqual(before, trial.manifest(root))
            receipt = load(output/qid/'raw-interruption-handoff.json')
            self.assertEqual(receipt['original_result']['status'], 'partial')
            self.assertEqual(receipt['original_result']['execution_report']['pending_passage_count'],16)
            self.assertFalse(receipt['semantic_complete']); self.assertFalse(receipt['budget_reset'])

    def test_absent_native_state_cannot_silently_collect(self):
        with TemporaryDirectory() as tmp, patch('ForecastAgent.agent.run_research') as collector:
            with self.assertRaisesRegex(ValueError,'preserved state'):
                trial.run(tmp,'41090')
            collector.assert_not_called()

    def test_missing_parent_archive_refuses_empty_state(self):
        a = trial.amendment()
        class Client:
            def api(self,url):
                if '/artifacts?' in url: return {'artifacts':[]}
                return {'status':'completed','head_sha':a['exact_parent_commit'],'run_attempt':1}
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'never start an empty ledger'):
                trial.restore(tmp,'41090',a['exact_parent_run_id'],Client())

    def test_completed_scores_are_verified_and_reused(self):
        _, c = trial.original.protocol(); qid = c['question_ids'][0]
        with TemporaryDirectory() as tmp:
            save(Path(tmp)/qid/'acquisition/state.json',{'stage':'complete'})
            with patch.object(trial.original,'run_case',return_value={'stage':'complete'}) as scoring, \
                 patch.object(trial.original.pipeline,'run') as acquire:
                self.assertEqual(trial.run(tmp,qid),{'stage':'complete'})
                acquire.assert_not_called(); scoring.assert_called_once()
