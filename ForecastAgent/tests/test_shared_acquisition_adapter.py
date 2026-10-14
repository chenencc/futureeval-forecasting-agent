"""Shared capabilities through actual competition hooks without provider requests."""
import copy,json,base64,hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from ForecastAgent.acquisition import eager_capture as eager
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.integrations.shared_acquisition import adapter
from ForecastAgent.acquisition_shared.contract import failure_details
from ForecastAgent.acquisition_shared.tool_output import compact_view


class SharedAdapter(unittest.TestCase):
    def task(self,root):
        return RetrievalTask(Path(root),{'id':'123','question':'Will aircraft enter ADIZ?',
            'resolution_criteria':'Use the specified official bulletin.','mode':'live','pipeline':'collection',
            'acquisition_profile':'collection_v3',eager.FIELD:eager.identity()})

    def test_native_alias_reuse_and_resume_make_no_fetch_reservation(self):
        with tempfile.TemporaryDirectory() as root,adapter():
            task=self.task(root);original='https://www.mnd.gov.tw/en/News/PLAAct/87977';alias=original.replace('/en/','/En/')
            task.bundle['pages'][original]={'url':original,'content':'PLA aircraft ADIZ bulletin. '*8,
                'retrieved_at_utc':'2026-10-14T00:00:00Z'}
            task.bundle['source_leads'][alias]={'url':alias,'origin':'page_link'};task.save()
            from ForecastAgent.runtime import retrieval
            with patch.object(retrieval,'fetch_public_page',side_effect=AssertionError('No network permitted')):
                value=task.execute('fetch_page',{'url':alias},None)
                self.assertIn('PLA aircraft',value['content'])
                restored=RetrievalTask(Path(root),task.bundle['request'])
                restored.execute('fetch_page',{'url':alias},None)
            self.assertEqual(len(restored.bundle['fetch_attempts']),0)
            self.assertEqual(restored.bundle['shared_source_reuse'][0]['source_capture_at'],'2026-10-14T00:00:00Z')

    def test_common_footer_role_is_consistent_and_short_notice_remains(self):
        with adapter():
            bad=eager.diagnostic({'content':'About\nPress\nCopyright\nContact us\nCreators\nAdvertise\nDevelopers\nTerms\nPrivacy'},'https://example.org/a')
            self.assertFalse(bad['usable_text'])
            good=eager.diagnostic({'content':'Agency operations were suspended on October 14,2026 due to a technical issue.'},'https://example.gov/notice')
            self.assertTrue(good['usable_text'])

    def test_large_coordinate_metadata_is_not_copied_into_model_reply(self):
        p={'content':'[Sheet1 row1] A1=Date','sheet_cell_view':{'raw_source_sha256':'a'*64,'rows':[{'large':'x'*200000}]},
           'sheet_cell_coverage':{'original_row_numbers':list(range(1000))}}
        before=copy.deepcopy(p);view=compact_view(p)
        self.assertEqual(p,before);self.assertNotIn('sheet_cell_view',view)
        self.assertTrue(view['sheet_cell_coverage']['original_row_numbers_projection_truncated'])
        self.assertLess(len(json.dumps(view)),4000)

    def test_wrapped_auth_failure_retains_stop_reason(self):
        cause=HTTPError('https://docs.google.com/',401,'Unauthorized',{},None)
        exc=RuntimeError('Preserved fetch failure');exc.__cause__=cause
        self.assertEqual(failure_details(exc)['failure_category'],'authentication_required')

    def test_capability_identity_is_inside_frozen_native_request(self):
        with tempfile.TemporaryDirectory() as root,adapter():
            task=self.task(root);task.save()
            self.assertIn('shared_acquisition',task.bundle['request'][eager.FIELD])
            self.assertIn('shared_host_adapter_sha256_lf',task.bundle['request'][eager.FIELD])
        with self.assertRaises(ValueError):
            with eager.adapter():eager.enabled(task)

    def test_sharing_action_never_consumes_a_fetch(self):
        with tempfile.TemporaryDirectory() as root,adapter():
            task=self.task(root)
            with self.assertRaises(ValueError):task.execute('fetch_page',{'url':'https://api.whatsapp.com/send?text=event'},None)
            self.assertEqual(task.bundle['fetch_attempts'],[])
