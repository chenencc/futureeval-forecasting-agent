"""Offline acquisition identity, shell and deterministic closure regressions."""
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from ForecastAgent.evidence.acquisition_quality import identity, page_form, discovery_score
from ForecastAgent.evidence.raw_capture import capture_report
from ForecastAgent.runtime.collection_actions import raw_stop_reason
from ForecastAgent.runtime.retrieval import run_retrieval
from ForecastAgent.tests import test_raw_recall
from ForecastAgent.tests.test_runtime_contracts import prepared, LIVE


class AcquisitionQualityTests(TestCase):
    def test_explicit_bill_identifier_beats_unrelated_official_pdf(self):
        request={'question':'Will SB 253 and SB 261 be in effect?'}
        wrong=discovery_score(request,'https://court.gov/23-2969.pdf','NetChoice v Bonta',True)
        right=discovery_score(request,'https://publisher.org/update','California SB 253 and SB 261 status')
        self.assertGreater(right,wrong)
        self.assertEqual(identity(request,'https://court.gov/23-2969.pdf','NetChoice v Bonta')['state'],'unmatched_candidate')
        self.assertEqual(identity(request,'https://publisher.org','SB 253')['matched_anchors'],['sb 253'])
        self.assertEqual(identity(request,'https://publisher.org','SB 2530')['matched_anchors'],[])

    def test_page_shell_flag_does_not_reject_short_announcement(self):
        shell='Ongoing health emergencies. The list below details events. Past health emergencies.'
        self.assertEqual(page_form('https://agency.org/emergencies/situations',{'content':shell})['state'],'possible_index_shell')
        self.assertEqual(page_form('https://agency.org/news/item/announcement',{'content':'Declared today.'})['state'],'document_candidate')
        self.assertEqual(page_form('https://agency.org/news',{'content':'17 May 2026: Declaration'})['state'],'document_candidate')
        self.assertEqual(page_form('https://agency.org/index',{'content':'|Date|Value|','rows':[{'date':'2026-08-03','value':5}]})['state'],'structured_data')

    def test_saved_originals_survive_unmatched_identity_and_shell_flags(self):
        with TemporaryDirectory() as root:
            task=test_raw_recall.RawRecallTests().task(root)
            before=deepcopy(task.bundle)
            result=capture_report(task.bundle)
            self.assertEqual(before,task.bundle)
            self.assertEqual(result['capture_count'],len(before['pages']))

    def test_stop_preserves_required_exa_and_rescue_opportunity(self):
        with TemporaryDirectory() as root:
            task=test_raw_recall.RawRecallTests().task(root)
            task.bundle['fetch_attempts']=[{'url':'https://example.org/'+str(i),'status':'completed'} for i in range(8)]
            task.bundle['search_policy']['exa']='required'
            self.assertIsNone(raw_stop_reason(task))
            task.bundle['exa_searches']=[{'status':'completed'}]
            self.assertEqual(raw_stop_reason(task),'raw_source_budget_exhausted')
            task.bundle['control']['no_progress_turns']=2
            with patch('ForecastAgent.runtime.collection_actions.primary_rescue',return_value=[{'url':'https://example.org/failed'}]):
                self.assertIsNone(raw_stop_reason(task))

    def test_corrupt_failed_capture_is_not_hidden_by_valid_successful_body(self):
        from ForecastAgent.tests.test_collection import page
        with TemporaryDirectory() as root:
            task=test_raw_recall.RawRecallTests().task(root)
            failed=page();failed['sha256']='corrupted'
            task.bundle['failed_captures']=[{'url':'https://example.org/blocked','page':failed,'reason':'Blocked body'}]
            report=capture_report(task.bundle)
            self.assertFalse(report['raw_integrity_passed'])
            self.assertTrue(report['integrity_failures'])

    def test_index_shell_has_gap_acceptance_but_keeps_original(self):
        from ForecastAgent.readers.loader import load_response
        from ForecastAgent.evidence.acceptance import collection_acceptance
        with TemporaryDirectory() as root:
            task=test_raw_recall.RawRecallTests().task(root)
            url='https://example.org/situations'
            source=load_response({'url':url,'final_url':url,'content_type':'text/plain',
                'raw':b'Ongoing emergencies. The list below details events.'},retrieved_at='2026-10-01T00:00:00Z')
            task.bundle['pages']={url:source}
            before=deepcopy(task.bundle['pages'])
            report=collection_acceptance(task.bundle)
            self.assertEqual(report['status'],'accepted_with_gaps')
            self.assertEqual(report['raw_capture_report']['possible_index_shell_count'],1)
            self.assertEqual(task.bundle['pages'],before)

    @patch('ForecastAgent.runtime.retrieval.ask_ultra',side_effect=AssertionError('No model calls authorized'))
    def test_exhausted_source_budget_exports_without_model_or_provider_calls(self,model):
        request={**deepcopy(LIVE),'acquisition_focus':'raw_recall'}
        with TemporaryDirectory() as root, patch('socket.socket.connect',side_effect=AssertionError('No network allowed')):
            task=prepared(root,request)
            task.bundle['fetch_attempts']=[{'url':'https://example.org/'+str(i),'status':'completed'} for i in range(8)]
            task.bundle['exa_searches']=[{'status':'completed','results':[]}]
            task.save()
            before=deepcopy(task.bundle['fetch_attempts'])
            result=run_retrieval(request,root,'','')
            model.assert_not_called()
            self.assertEqual(result['result']['termination_reason'],'raw_source_budget_exhausted')
            self.assertEqual(result['fetch_attempts'],before)
            self.assertFalse(result['result']['incomplete'] if 'incomplete' in result['result'] else False)

    def test_stall_stop_is_not_completeness_or_model_gap_judgment(self):
        with TemporaryDirectory() as root:
            task=test_raw_recall.RawRecallTests().task(root)
            task.bundle['control']['no_progress_turns']=1
            self.assertIsNone(raw_stop_reason(task))
            task.bundle['control']['no_progress_turns']=2
            self.assertEqual(raw_stop_reason(task),'raw_no_progress_limit')
