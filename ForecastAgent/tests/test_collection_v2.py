import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from datetime import datetime,timezone

from ForecastAgent.runtime.collection_v2 import masked,late_dates,model_view
from ForecastAgent.runtime.retrieval import RetrievalTask


class CollectionV2Tests(TestCase):
    def test_dynamic_summary_and_exact_offsets(self):
        cutoff=datetime(2026,8,20,tzinfo=timezone.utc)
        text='Stable legal definition.\nPublished: 4 September 2026 future update\nAnother stable paragraph.'
        visible,ranges=masked(text,cutoff)
        self.assertEqual(len(visible),len(text))
        self.assertNotIn('future update',visible)
        self.assertIn('Stable legal definition',visible)
        self.assertTrue(late_dates('September 4, 2026',cutoff))
        self.assertTrue(ranges)

    @patch('ForecastAgent.retrieval_agent.search_batch')
    def test_budget_resume_and_inline_quarantine(self,search):
        with TemporaryDirectory() as temp:
            request={'question':'Question','resolution_criteria':'Official announcement','as_of_utc':'2026-08-20T00:00:00Z','pipeline':'collection','acquisition_profile':'collection_v2'}
            task=RetrievalTask(Path(temp),request)
            task.bundle['plan']=[{'id':'n','priority':'critical'}]
            search.return_value={'results':[{'url':'https://example.org/topic','published_date':'2023-01-01T00:00:00Z','content':'Published: 4 September 2026 announcement'}]}
            args={'query':'official update','need_ids':['n'],'reason':'Missing source','search_role':'recent'}
            result=task.execute('search_tavily',args,'key')
            self.assertEqual(result['results'],[])
            self.assertEqual(len(task.bundle['quarantine']),1)
            self.assertEqual(search.call_args.kwargs['start_date'],'2026-06-21')
            self.assertEqual(RetrievalTask(Path(temp),request).budget()['tavily_basic_remaining'],4)
            task.bundle['searches'] += [{'results':[]}]*2
            with self.assertRaises(ValueError):task.execute('search_tavily',{**args,'search_role':'gap'},'key')
            task.execute('search_tavily',args,'key')
            task.execute('search_tavily',args,'key')
            with self.assertRaises(ValueError):task.execute('search_tavily',args,'key')

    def test_batch_location_and_recording_respect_blocked_ranges(self):
        with TemporaryDirectory() as temp:
            request={'question':'Question','resolution_criteria':'Rules','as_of_utc':'2026-08-20T00:00:00Z','pipeline':'collection','acquisition_profile':'collection_v2','historical_body_policy':'date_filtered_exploratory'}
            task=RetrievalTask(Path(temp),request);task.bundle['plan']=[{'id':'n','priority':'critical'}]
            text='The official regulation sets the eligibility requirement and reporting rules. '+ 'Background explanatory material. '*5
            text+='\nPublished: 4 September 2026 the new regulation changes the requirement.'
            task.bundle['pages']['https://example.org/rule']={'content':text,'sha256':'hash','temporal_status':'current_capture_possible_later_edits'}
            result=task.execute('read_sources',{'urls':[],'queries':[{'query':'regulation eligibility requirement','need_ids':['n']}]},'')
            passage=result['located_material'][0]['passages'][0]
            stored=task.execute('record_excerpts',{'items':[passage['excerpt_args']]},'')
            self.assertTrue(stored['items'][0]['ok'])
            bad={'url':'https://example.org/rule','start_char':text.index('Published'),'end_char':len(text),'need_ids':['n']}
            self.assertFalse(task.execute('record_excerpts',{'items':[bad]},'')['items'][0]['ok'])

    def test_compact_projection_keeps_original_unchanged(self):
        raw={'data':{'content':'x'*6000,'raw_response':{'secret_provider_body':'original'}}}
        view=model_view(raw)
        self.assertEqual(len(view['data']['content']),2400)
        self.assertNotIn('raw_response',view['data'])
        self.assertEqual(len(raw['data']['content']),6000)
