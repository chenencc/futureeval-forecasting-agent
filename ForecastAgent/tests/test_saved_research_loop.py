"""Saved-document loop regression checks; no provider requests are made."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.supplement.research_loop import SavedTools, Transport, LIMITS, run, audit_proposals
from ForecastAgent.tests.test_material_review_only import ReviewOnlyTests


class SavedResearchTests(unittest.TestCase):
    def test_complete_rows_and_exact_body_ranges(self):
        body='date | value\n2026-03-01 | 1\n2026-04-01 | 2\n'
        b={'pages':{'https://example.org/data':{'content':body}},'plan':[],'request':{'id':1}}
        s={};tools=SavedTools(b,s);ident=next(iter(tools.catalog))
        rows=tools.execute({'tool':'read_table_rows','document_id':ident,'row_start':1,'row_end':3})['rows']
        self.assertEqual(rows['text'],body[rows['start']:rows['end']])
        self.assertEqual(rows['text'],'2026-03-01 | 1\n2026-04-01 | 2\n')
        with self.assertRaises(ValueError):tools.execute({'tool':'fetch','url':'https://example.org'})
        with self.assertRaises(ValueError):tools.execute({'tool':'validate_binding','need_id':'fake','passage_id':'fake'})

    def test_physical_reservation_cap_is_before_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            state={'decisions':[],'model_attempts':[{'status':'received'}]*LIMITS['stage_http'],'prior_decisions':0,'prior_http':0}
            def model(messages,key,**kw):
                kw['observer']('reserve',{'status':'reserved','request':{'model':'nvidia/nemotron-3-ultra-550b-a55b:free'}})
                self.fail('No HTTP request may follow a rejected reservation')
            with patch('ForecastAgent.providers.model.ask_model',side_effect=model):
                with self.assertRaisesRegex(RuntimeError,'physical_budget_exhausted'):
                    Transport(Path(tmp),state,'dummy').call('step','system',{}, {})
            self.assertEqual(len(state['model_attempts']),18)
            self.assertEqual(state['decisions'][0]['status'],'failed')

    def test_saved_only_run_preserves_parents_and_idempotence(self):
        def model(messages,key,**kw):
            p=json.loads(messages[-1]['content'])
            msg={'content':json.dumps({'tools':[],'assessments':[{'need_id':n['id'],'status':'uncertain',
                'passage_ids':[],'reason':'Saved material is insufficient.'} for n in p['needs']],
                'finish':True,'reason':'Explicit gaps remain.'})}
            token=kw['observer']('reserve',{'status':'reserved','request':{'model':'nvidia/nemotron-3-ultra-550b-a55b:free'}})
            kw['observer']('complete',{'status':'received','request':{'model':'nvidia/nemotron-3-ultra-550b-a55b:free'},
                'response':{'choices':[{'message':msg,'finish_reason':'stop'}],'usage':{'total_tokens':10}}},token)
            return msg
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=root/'parent';prior=root/'prior';out=root/'out'
            ReviewOnlyTests().parent(parent)
            save(prior/'identity.json',{'parent':True});save(prior/'result.json',{'old':True})
            save(prior/'state.json',load(parent/'supplement/state.json'))
            with patch('ForecastAgent.providers.model.ask_model',side_effect=model) as mocked:
                result=run(parent,prior,out,'dummy')
                self.assertTrue(result['parent_unchanged'])
                self.assertEqual(result['new_http_attempts'],1)
                self.assertEqual(result['new_search_calls'],0)
                self.assertEqual(result['accepted_need_ids'],[])
                self.assertEqual(run(parent,prior,out,'dummy'),result)
                self.assertEqual(mocked.call_count,1)

    def test_critic_rejects_proposal_without_closing_need(self):
        class Critic:
            def call(self,*args):return {'checks':[{'need_id':'n','status':'unsupported','issue':'wrong_document_role','reason':'An opinion is not a docket.'}]}
        state={'proposals':{'n':{'url':'https://example.org','quote':'opinion'}},'audited':{},'accepted':{'n':{}},'issues':{}}
        audit_proposals({'request':{},'pages':{'https://example.org':{'content':'opinion'}}},[{'id':'n'}],state,Critic())
        self.assertNotIn('n',state['accepted'])
        self.assertEqual(state['issues']['n']['repair_rounds'],0)
