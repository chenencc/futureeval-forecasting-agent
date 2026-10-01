"""Current acquisition relaxes dates without restoring consumed allowances."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.temporal_policy import amend_current, CURRENT
from ForecastAgent.runtime.guidance import collection_system
from ForecastAgent.evidence.acceptance import acquisition_metrics
from ForecastAgent.evidence.intelligence import intelligence_package
from ForecastAgent.runtime.session import cache_key
from ForecastAgent.debug_trial import run_one

REQUEST = {'question':'Is a product available?', 'resolution_criteria':'Official release before September 2026',
    'as_of_utc':'2026-07-09T00:00:00Z', 'pipeline':'collection', 'acquisition_profile':'collection_v3'}
URL = 'https://example.org/release'
TEXT = 'Published: September 25, 2026. The product release announcement contains documentation and platform requirements. '


class TemporalPolicyTests(TestCase):
    @patch.dict('os.environ', {'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.search_exa')
    @patch('ForecastAgent.runtime.retrieval.search_batch')
    def test_both_searches_omit_cutoff_and_accept_late_or_unknown_dates(self, tavily, exa):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp), {**REQUEST,'collection_temporal_policy':CURRENT})
            task.bundle['plan']=[{'id':'n','priority':'critical'}]
            tavily.return_value={'results':[{'url':URL,'content':TEXT}]}
            args={'query':'product release','need_ids':['n'],'reason':'Find documentation','search_role':'recent'}
            self.assertTrue(task.execute('search_tavily',args,'test-only')['results'])
            self.assertIsNone(tavily.call_args.kwargs['end_date'])
            self.assertNotIn('start_date',tavily.call_args.kwargs)
            exa.return_value={'results':[{'url':URL+'/independent','published_date':'2026-09-25T00:00:00Z'}],
                'raw_response':{},'request_payload':{}}
            self.assertTrue(task.execute('search_exa',args,'')['results'])
            self.assertIsNone(exa.call_args.kwargs['cutoff'])
            self.assertIsNone(exa.call_args.kwargs['start'])
            self.assertFalse(task.bundle['quarantine'])
            self.assertEqual(task.bundle['acquisition_limits'],{'tavily_basic':3,'exa_search':1})
            with self.assertRaises(ValueError):
                task.execute('search_exa',args,'')

    @patch.dict('os.environ', {'EXA_API_KEY':'test-only'})
    def test_amendment_reuses_saved_body_preserves_dates_budgets_and_old_result(self):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST)
            task.bundle['plan']=[{'id':'n','priority':'critical'}]
            task.bundle['pages'][URL]={'url':URL,'content':TEXT,'sha256':'original',
                'retrieved_at_utc':'2026-10-01T00:00:00Z','temporal_status':'current_capture_possible_later_edits'}
            task.bundle['searches']=[{'results':[],'status':'completed'} for _ in range(3)]
            task.bundle['exa_searches']=[{'results':[],'status':'completed'}]
            task.bundle['result']={'incomplete':False,'summary':'Old cutoff gaps'}
            original=deepcopy(task.bundle)
            old_key=cache_key(task,'read_document',{'url':URL})
            self.assertTrue(task.page_view(task.bundle['pages'][URL])['blocked'])
            self.assertTrue(amend_current(task.bundle,'User waived cutoff for acquisition debugging'))
            task.save()
            resumed=RetrievalTask(Path(temp),REQUEST)
            self.assertIsNone(resumed.cutoff)
            self.assertFalse(resumed.verified_only)
            self.assertEqual(resumed.bundle['mode'],'live')
            self.assertEqual(resumed.page_view(resumed.bundle['pages'][URL])['content'],TEXT)
            self.assertNotEqual(old_key,cache_key(resumed,'read_document',{'url':URL}))
            for key in ('request','request_hash','acquisition_limits','searches','exa_searches','pages'):
                self.assertEqual(original[key],resumed.bundle[key])
            self.assertEqual(resumed.budget()['tavily_basic_remaining'],0)
            self.assertEqual(resumed.budget()['exa_search_remaining'],0)
            self.assertEqual(resumed.bundle['result_history'][0]['result'],original['result'])
            resumed.bundle['result']={'incomplete':False,'summary':'New output'}
            self.assertFalse(amend_current(resumed.bundle,'Repeated operator flag'))
            self.assertEqual(resumed.bundle['result']['summary'],'New output')
            self.assertEqual(acquisition_metrics(resumed.bundle)['usable_body_count'],1)
            package=intelligence_package(resumed.bundle)
            self.assertEqual(package['collection_temporal_policy'],CURRENT)
            self.assertFalse(package['temporal_provenance']['cutoff_enforced'])
            self.assertFalse(package['temporal_provenance']['historical_clean'])
            system=collection_system(resumed,[])
            self.assertIn('provenance only',system)
            self.assertNotIn('as_of_utc cutoff is the simulated present',system)

    def test_existing_unamended_cutoff_remains_enforced(self):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST)
            task.save()
            resumed=RetrievalTask(Path(temp),REQUEST)
            self.assertIsNotNone(resumed.cutoff)
            self.assertTrue(resumed.verified_only)
            with self.assertRaises(ValueError):
                RetrievalTask(Path(temp),{**REQUEST,'collection_temporal_policy':CURRENT})

    def test_explicit_debug_amendment_changes_only_selected_task(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'batch.json').write_text(json.dumps({'tasks':['1','2']}),encoding='utf-8')
            for qid in ('1','2'):
                task=RetrievalTask(root/'tasks'/qid,{**REQUEST,'id':qid})
                task.bundle['result']={'incomplete':False,'summary':'Preserved result'}
                task.save()
            other=(root/'tasks'/'2'/'bundle.json').read_bytes()
            def resume(request,directory,*keys):
                task=RetrievalTask(directory,request)
                self.assertIsNone(task.cutoff)
                task.bundle['result']={'incomplete':False,'summary':'Current-information result'}
                task.save()
                return task.bundle
            with patch('ForecastAgent.debug_trial.run_retrieval',side_effect=resume):
                summary=run_one(root,'1','test-only','test-only',current_information=True)
            self.assertTrue(all(summary['ledger_checks'].values()))
            self.assertEqual(summary['tavily_increment'],0)
            self.assertEqual(summary['exa_increment'],0)
            self.assertEqual(other,(root/'tasks'/'2'/'bundle.json').read_bytes())
            with self.assertRaises(ValueError):
                run_one(root,'1','test-only','test-only',current_information=True)
