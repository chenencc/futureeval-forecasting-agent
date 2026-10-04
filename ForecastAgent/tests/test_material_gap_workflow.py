"""Gap acquisition closes operational loops without interpreting outcomes."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load
from ForecastAgent.supplement import enhanced,need_ledger,gap_search
from ForecastAgent.runtime.retrieval import RetrievalTask,run_retrieval
from ForecastAgent.tests.test_collection import call


def bundle():
    return {'request':{'id':'test','question':'When will a publicly available LLM achieve gold on the International Math Olympiad?',
        'resolution_criteria':'A publicly queryable LLM must achieve gold.',
        'experiment_id':'gap-test','pipeline':'collection','collection_workflow':'material-gap-v1'},
        'plan':[{'id':'access','priority':'critical','condition':'Publicly available LLM browser or API access notice',
                 'query':'LLM International Math Olympiad gold public API release'}],
        'pages':{'https://lab.example/evaluation':{'content':('LLM International Math Olympiad gold report. '
            'We will be making a version available to subscribers before rolling it out. '*5),'links':[]}},
        'searches':[],'exa_searches':[],'fetch_attempts':[]}


class MaterialGapTests(unittest.TestCase):
    def test_missing_family_launch_read_does_not_require_contest_in_title(self):
        b=bundle()
        search=lambda tool,query:{'results':[{'url':'https://lab.example/launch','title':'Gemini browser API release'}]}
        body={'content':('Gemini Deep Think is now available today via browser and API. '*10),'links':[]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(enhanced,'fetch_document',return_value=body) as http:
            out=enhanced.run(b,tmp,network=True,search=search,max_link_depth=2)
            self.assertEqual(http.call_count,1)
            candidate=out['material_need_ledger']['needs'][0]['candidates'][0]
            self.assertTrue(candidate['discovery_need_association'])
            self.assertFalse(candidate['subject_binding_verified'])
            self.assertFalse(candidate['truth_verified'])

    def test_empty_discovery_is_distinct_and_not_retried_after_resume(self):
        b=bundle();calls=[]
        def search(tool,query):calls.append(tool);return {'results':[]}
        with tempfile.TemporaryDirectory() as tmp:
            out=enhanced.run(b,tmp,network=True,search=search)
            self.assertEqual(calls,['tavily','exa'])
            self.assertEqual(out['material_need_ledger']['needs'][0]['status'],'no_source_returned')
            enhanced.run(b,tmp,network=True,search=search)
            self.assertEqual(calls,['tavily','exa'])

    def test_foreign_language_data_uses_station_identifier_not_english_topic_words(self):
        b={'request':{'question':'What will the water level at Kaub on the Rhine be on September 1, 2026?',
             'resolution_criteria':'Station 25700100 in cm at 08:00 CEST.'},
           'plan':[{'id':'level','condition':'Water level measurement','priority':'critical'}],
           'pages':{'https://official.example/down.txt':{'content':
               '01.09.2026\nRHEIN\nKAUB\n25700100\ncm\n'+('07:00#69\n08:00#68\n'*35)}}}
        row=need_ledger.build(b)['needs'][0]
        self.assertEqual(row['status'],'candidate_captured')
        self.assertFalse(row['semantic_verified'])

    def test_fourth_gap_search_allowed_only_in_opted_in_experiment(self):
        q={**bundle()['request'],'mode':'live','acquisition_focus':'raw_recall',
           'acquisition_profile':'collection_v3','budget_profile':'solid_v2',
           'collection_temporal_policy':'current_information'}
        args={'query':'LLM International Math Olympiad gold API release announcement',
              'need_ids':['access'],'reason':'Missing public access notice','search_role':'gap'}
        with patch('ForecastAgent.runtime.retrieval.search_batch',return_value={'results':[]}) as provider:
            for enabled in (False,True):
                with tempfile.TemporaryDirectory() as tmp:
                    request=copy.deepcopy(q)
                    if not enabled:request.pop('collection_workflow')
                    task=RetrievalTask(Path(tmp),request);task.bundle['plan']=bundle()['plan']
                    task.bundle['searches']=[{'status':'failed','results':[]} for _ in range(3)]
                    if enabled:
                        task.execute('search_tavily',args,'test-key')
                        self.assertEqual(len(task.bundle['searches']),4)
                    else:
                        with self.assertRaisesRegex(ValueError,'Searches four'):
                            task.execute('search_tavily',args,'test-key')
                        self.assertEqual(len(task.bundle['searches']),3)
            self.assertEqual(provider.call_count,1)

    def test_future_rollout_is_not_current_access_and_no_truth_claim(self):
        b=bundle();before=copy.deepcopy(b)
        ledger=need_ledger.build(b)
        self.assertFalse(ledger['needs'][0]['candidates'])
        self.assertEqual(ledger['needs'][0]['status'],'unlocated')
        b['pages']['https://lab.example/launch']={'content':('LLM International Math Olympiad gold system is now available today via browser and API. '*8)}
        row=need_ledger.build(b)['needs'][0]
        self.assertEqual(row['status'],'candidate_captured');self.assertFalse(row['semantic_verified'])
        self.assertFalse(row['candidates'][0]['truth_verified'])
        self.assertEqual(before['plan'],b['plan'])

    def test_existing_sources_do_not_block_gap_search_and_restart_does_not_repeat(self):
        b=bundle();before=copy.deepcopy(b)
        search=lambda tool,query:{'results':[{'url':'https://lab.example/access','title':'LLM International Math Olympiad gold public API launch'}]}
        body={'content':('LLM International Math Olympiad gold system is now available today via API and browser. '*8),'links':[]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(enhanced,'fetch_document',return_value=body) as http:
            with patch('builtins.print'):
                out=enhanced.run(b,tmp,network=True,search=search,max_link_depth=2)
            journal=load(Path(tmp)/'state.json')
            self.assertEqual(sum(a['tool']=='tavily' for a in journal['attempts']),1)
            self.assertEqual(http.call_count,1)
            self.assertEqual(out['material_need_ledger']['needs'][0]['status'],'candidate_captured')
            self.assertEqual(out['material_termination']['critical_unlocated_need_ids'],['access'])
            self.assertFalse(out['material_need_ledger']['needs'][0]['target_material_captured'])
            enhanced.run(b,tmp,network=True,search=search,max_link_depth=2)
            self.assertEqual(http.call_count,1)
            self.assertEqual(load(Path(tmp)/'state.json'),journal)
        self.assertEqual(b,before)

    def test_shared_reserved_budgets_block_search_and_network(self):
        b=bundle();b['searches']=[{'status':'reserved'}]*3;b['exa_searches']=[{'status':'failed'}]
        with tempfile.TemporaryDirectory() as tmp,patch.object(enhanced,'fetch_document') as http:
            def fail(*args):raise AssertionError('Provider budget must not restart')
            out=enhanced.run(b,tmp,network=True,search=fail)
            self.assertFalse(http.called)
            self.assertEqual(out['material_need_ledger']['needs'][0]['status'],'budget_exhausted')
            self.assertEqual(out['material_termination']['reason'],'discovery_capacity_exhausted')

    def test_provider_block_stops_cross_provider_attempts(self):
        b=bundle()
        class Blocked(RuntimeError):http_status=429
        with tempfile.TemporaryDirectory() as tmp,patch.object(enhanced,'fetch_document'):
            def search(*args):raise Blocked('account quota')
            out=enhanced.run(b,tmp,network=True,search=search)
            journal=load(Path(tmp)/'state.json')
            self.assertEqual(len(journal['attempts']),1)
            self.assertTrue(journal['provider_blocked'])
            self.assertEqual(out['material_termination']['reason'],'provider_blocked')

    def test_callback_one_basic_request_no_retry_and_missing_key_skipped(self):
        with patch.object(gap_search,'search_batch',return_value={'results':[]}) as tavily,patch.object(gap_search,'exa_search') as exa:
            fn=gap_search.callback('test-key',None)
            self.assertEqual(fn.available_tools,{'tavily'})
            fn('tavily','subject public API launch')
            tavily.assert_called_once_with('subject public API launch','test-key',topic='general')
            with self.assertRaises(ValueError):fn('exa','query')
            exa.assert_not_called()

    def test_program_reads_exact_discovered_batch_without_model_copy_step(self):
        q={**bundle()['request'],'mode':'live','acquisition_focus':'raw_recall',
           'acquisition_profile':'collection_v3','exa_search_policy':'optional',
           'collection_temporal_policy':'current_information'}
        with tempfile.TemporaryDirectory() as tmp:
            task=RetrievalTask(Path(tmp),q);task.bundle['plan']=bundle()['plan']
            task.bundle['searches']=[{'status':'completed','results':[{'url':'https://lab.example/access','title':'LLM International Math Olympiad API'}]}]
            task.save()
            reply={'content':('LLM International Math Olympiad system available today via browser and API. '*8),'links':[]}
            with patch('ForecastAgent.runtime.retrieval.fetch_public_page',return_value=reply) as http,patch('ForecastAgent.runtime.retrieval.ask_ultra',return_value=call('finish_collection',{'gaps':[]},'finish')) as model:
                out=run_retrieval(q,tmp,'','')
            self.assertEqual(http.call_count,1)
            self.assertEqual(model.call_count,1)
            self.assertEqual(out['sessions'][-1]['program_read_batches'],1)
            self.assertEqual(out['sessions'][-1]['model_decisions'],1)
            self.assertEqual(out['step_attempts'][0]['owner'],'program')
            self.assertTrue((Path(tmp)/'material-needs.json').exists())


if __name__=='__main__':unittest.main()
