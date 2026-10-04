"""Offline regression gates for generic material acquisition and safe handoff."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load
from ForecastAgent.tests.test_material_gap_workflow import bundle
from ForecastAgent.supplement import need_ledger, material_review, enhanced, frontier
from ForecastAgent.runtime.search_contract import extra_roles, description
from ForecastAgent.runtime.retrieval import RetrievalTask, COLLECTION_TOOLS
from ForecastAgent.runtime.tool_selection import active_tools


class MaterialFitTests(unittest.TestCase):
    def notice(self):
        b=bundle()
        url='https://lab.example/launch'
        text='The evaluated LLM International Math Olympiad system is not released to the public. '
        b['pages'][url]={'content':text*8,'links':[]}
        return b,url,text

    def test_keyword_candidate_never_closes_missing_material(self):
        b=bundle()
        b['pages']['https://lab.example/report']={'content':
            'LLM International Math Olympiad gold report. Our funding is now available today via API. '*8}
        ledger=need_ledger.build(b)
        self.assertTrue(ledger['needs'][0]['candidates'])
        self.assertFalse(ledger['needs'][0]['target_material_captured'])
        self.assertIsNotNone(need_ledger.next_search(ledger,{'attempts':[]},{'tavily':0,'exa':0},{'tavily':3,'exa':1}))
        self.assertEqual(need_ledger.termination(ledger,reason='done',search_available=True)['acquisition_outcome'],'material_unlocated')

    def test_negative_notice_can_be_material_without_yes_no_verdict(self):
        b,url,text=self.notice()
        payload=material_review.packet(b,need_ledger.build(b),[])
        result=material_review.bind(b,payload,{'bindings':[{'need_id':'access','url':url,'quote':text,
            'axes':{a:True for a in material_review.AXES}}],'priority_urls':[]})
        journal={'material_reviews':[{'result':result}]}
        row=need_ledger.build(b,journal)['needs'][0]
        self.assertTrue(row['target_material_captured'])
        self.assertEqual(row['acquisition_state'],'target_material_captured')
        self.assertFalse(row['semantic_verified'])
        self.assertFalse(row['material_bindings'][0]['truth_verified'])
        b['pages'][url]['content']+='changed'
        self.assertFalse(need_ledger.build(b,journal)['needs'][0]['target_material_captured'])

    def test_mismatched_subject_and_fabricated_quotes_do_not_bind(self):
        b,url,text=self.notice();payload=material_review.packet(b,need_ledger.build(b),[])
        row={'need_id':'access','url':url,'quote':text,'axes':{a:True for a in material_review.AXES}}
        row['axes']['entity']=False
        result=material_review.bind(b,payload,{'bindings':[row]})
        self.assertFalse(need_ledger.build(b,{'material_reviews':[{'result':result}]})['needs'][0]['target_material_captured'])
        row['quote']='A fabricated claim with no exact original quotation.'
        with self.assertRaisesRegex(ValueError,'Quotation'):
            material_review.bind(b,payload,{'bindings':[row]})
        with self.assertRaisesRegex(ValueError,'catalog'):
            material_review.bind(b,payload,{'priority_urls':['https://invented.example/data']})

    def test_agent_review_binding_persists_on_resume_without_repeated_calls(self):
        b,url,text=self.notice();calls=[]
        def agent(payload,state,folder):
            calls.append(payload)
            return {'bindings':[{'need_id':'access','url':url,'quote':text,
                'axes':{a:True for a in material_review.AXES}}],'priority_urls':[]}
        with tempfile.TemporaryDirectory() as tmp:
            out=enhanced.run(b,tmp,network=True,material_agent=agent)
            self.assertEqual(out['material_termination']['acquisition_outcome'],'materials_ready')
            enhanced.run(b,tmp,network=True,material_agent=agent)
            self.assertEqual(len(calls),1)
            self.assertEqual(load(Path(tmp)/'state.json')['material_reviews'][0]['status'],'bound')

    def test_rejected_agent_payload_is_not_retried_and_does_not_close_gap(self):
        b=bundle();calls=[]
        def agent(*args):calls.append(1);return {'priority_urls':['https://invented.example/page']}
        with tempfile.TemporaryDirectory() as tmp:
            out=enhanced.run(b,tmp,network=True,material_agent=agent)
            self.assertEqual(out['material_termination']['critical_unlocated_need_ids'],['access'])
            enhanced.run(b,tmp,network=True,material_agent=agent)
            self.assertEqual(calls,[1])
            self.assertTrue(load(Path(tmp)/'state.json')['material_agent_stopped'])

    def test_role_schema_and_execution_share_the_same_contract(self):
        q={**bundle()['request'],'mode':'live','acquisition_focus':'raw_recall',
           'acquisition_profile':'collection_v3','budget_profile':'solid_v2',
           'collection_temporal_policy':'current_information'}
        with tempfile.TemporaryDirectory() as tmp:
            task=RetrievalTask(Path(tmp),q);task.bundle['plan']=bundle()['plan']
            task.bundle['searches']=[{'status':'failed','results':[]} for _ in range(3)]
            tool=active_tools(task,COLLECTION_TOOLS,'search_tavily')[0]['function']
            self.assertEqual(tool['parameters']['properties']['search_role']['enum'],list(extra_roles(task.bundle)))
            self.assertIn('gap, crosscheck',tool['description'])
            self.assertIn('gap, crosscheck',description(task.bundle,task.search_limit))

    def test_background_navigation_is_deferred_without_hiding_raw_sources(self):
        b=bundle();q=b['request']
        url='https://example.org/about/team'
        source={'url':url,'rule_primary':False,'score':100,'origin':'saved_link','parent_url':'https://example.org/',
            'label':'LLM International Math Olympiad','material_dependency':{'role':'ordinary_detail'},
            'source_contract':{'topic_matches':['llm','olympiad']}}
        result=frontier.admit([source],q)
        self.assertEqual(result['accepted'],[])
        self.assertEqual(result['deferred'][0]['deferred_reason'],'background_navigation_route')
        source['rule_primary']=True
        self.assertEqual(frontier.admit([source],q)['accepted'],[source])

    def test_exhausted_shared_model_capacity_sends_no_request(self):
        b=bundle();b['capacity']={'model_http_lifetime':1,'model_http_dispatch':1,'model_decisions':1}
        b['model_attempts']=[{'status':'received'}]
        b['sessions']=[{'attempts_before':0,'model_decisions':1}]
        fn=material_review.callback('not-a-secret',b)
        with tempfile.TemporaryDirectory() as tmp,patch('ForecastAgent.providers.model.ask_model') as model:
            with self.assertRaisesRegex(RuntimeError,'capacity'):
                fn(material_review.packet(b,need_ledger.build(b),[]),{},tmp)
            model.assert_not_called()

    def test_no_progress_stops_host_branch_but_preserves_rule_source(self):
        b=bundle();b['pages']={}
        root='https://lab.example/primary'
        b['request']['resolution_criteria']='Public access notice from '+root
        b['source_leads']={f'https://lab.example/{i}':{
            'url':f'https://lab.example/{i}', 'title':'LLM International Math Olympiad context','origin':'missing_family_search','need_ids':['access']}
            for i in ('alpha','beta','gamma','delta','epsilon','zeta')}
        body={'content':'This is a background discussion of the International Math Olympiad. '*8,'links':[]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(enhanced,'fetch_document',return_value=body) as fetch:
            out=enhanced.run(b,tmp,network=True)
            urls=[c.args[0] for c in fetch.call_args_list]
            self.assertIn(root,urls)
            self.assertLess(len(urls),7)
            self.assertTrue(any(r['deferred_reason']=='host_material_no_progress' for r in out['enhanced_supplement']['deferred_candidates']))
            self.assertTrue(out['material_termination']['critical_unlocated_need_ids'])

    def test_agent_search_priorities_are_applied_before_new_page_capture(self):
        b=bundle();b['pages']={}; seen=[]
        bad='https://other.example/context'; good='https://lab.example/access'
        def search(tool,query):return {'results':[{'url':bad,'title':'LLM International Math Olympiad background'},
                                                 {'url':good,'title':'LLM International Math Olympiad release'}]}
        def agent(payload,state,folder):
            urls={r['url'] for r in payload['sources']}
            return {'bindings':[], 'priority_urls':[good] if good in urls else [],
                    'deferred_urls':[{'url':bad,'reason':'Background document, not an access notice'}] if bad in urls else []}
        def fetch(url):seen.append(url);return {'content':('LLM International Math Olympiad access notice. '*10),'links':[]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(enhanced,'fetch_document',side_effect=fetch):
            out=enhanced.run(b,tmp,network=True,search=search,material_agent=agent)
            self.assertEqual(seen,[good])
            self.assertTrue(any(r['deferred_reason']=='agent_document_fit_deferral' for r in out['enhanced_supplement']['deferred_candidates']))

    def test_physical_model_attempts_are_reserved_and_retries_cannot_exceed_shared_cap(self):
        b=bundle();b['model_attempts']=[{'status':'received'}]
        b['capacity']={'model_http_lifetime':2,'model_http_dispatch':2,'model_decisions':4,'model_failures':4}
        fn=material_review.callback('not-a-secret',b)
        state={}
        def model(messages,key,**kwargs):
            observe=kwargs['observer']
            token=observe('reserve',{'status':'reserved'})
            observe('complete',{'status':'http_error','response':{}},token)
            observe('reserve',{'status':'reserved'})
        with tempfile.TemporaryDirectory() as tmp,patch('ForecastAgent.providers.model.ask_model',side_effect=model):
            with self.assertRaisesRegex(RuntimeError,'HTTP capacity'):
                fn(material_review.packet(b,need_ledger.build(b),[]),state,tmp)
            self.assertEqual(len(state['material_model_attempts']),1)
            self.assertTrue(state['material_model_attempts'][0]['usage_unknown'])
            self.assertEqual(state['material_reviews'][0]['status'],'failed')


if __name__=='__main__':unittest.main()
