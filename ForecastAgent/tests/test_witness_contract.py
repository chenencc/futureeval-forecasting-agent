"""Coverage completeness and explicit document identity without provider calls."""
import unittest
import json
import tempfile
from unittest.mock import patch
from ForecastAgent.supplement import witness_contract as contract
from ForecastAgent.supplement import material_review
from ForecastAgent.supplement import binding_guard,requirement_contract


class WitnessContractTests(unittest.TestCase):
    def test_event_date_has_no_numeric_axis_but_publication_needs_its_own_clock(self):
        event={'condition':'The election was held before September 1, 2026.'}
        publication={'condition':'Results were known and published before September 8, 2026.'}
        self.assertNotIn('metric',binding_guard.required_axes(event))
        self.assertNotIn('metric',binding_guard.required_axes(publication))
        self.assertIn('metric',binding_guard.required_axes({'condition':'Number of seats published before September 8, 2026.'}))
        self.assertIn('publication_time_witness_unverified',requirement_contract.issues(publication,{'quote':'Election date: 23 August 2026. Five parties won seats.'}))
        self.assertEqual(requirement_contract.issues(publication,{'quote':'Published: 2026-08-26. Five parties won seats.'}),[])
        self.assertIn('publication_before_deadline_unverified',requirement_contract.issues(publication,{'quote':'Published: 2026-09-09. Five parties won seats.'}))

    def test_callback_sends_exhaustive_schema_and_decodes_all_need_states(self):
        payload={'coverage_protocol':contract.PROTOCOL,'needs':[{'id':'date'},{'id':'publication'}],
                 'passages':[{'passage_id':'P1'}],'sources':[]}
        assessments=[{'need_id':n,'status':'uncertain','passage_ids':['P1'],'reason':'Needs examination.'}
                     for n in ('date','publication')]
        response={'bindings':[],'priority_source_ids':[],'deferred_source_ids':[],
                  'next_search':None,'need_assessments':assessments}
        def fake(messages,key,**kwargs):
            self.assertIn('need_assessments',kwargs['tools'][0]['function']['parameters']['required'])
            self.assertIn('EVERY need',messages[0]['content'])
            return {'content':json.dumps(response)}
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'FORECAST_MODEL':'nvidia/nemotron-3-super-120b-a12b:free'}),patch('ForecastAgent.providers.model.ask_model',side_effect=fake):
            agent=material_review.callback('test-key',{'capacity':{'model_decisions':1}},max_reviews=1,review_retry_seconds=180)
            result=agent(payload,{},folder)
        self.assertEqual(result['need_assessments'],assessments)

    def test_named_form_is_not_generic_ipo_preparation(self):
        need={'condition':'News indicate intent to file an S-1 registration.'}
        self.assertIn('required_form_identifier_unobserved',contract.issues(need,{'quote':'Issuer is preparing a potential IPO.'}))
        self.assertEqual(contract.issues(need,{'quote':'Issuer plans to file an S\u20111 before July.'}),[])
        self.assertEqual(contract.issues({'condition':'IPO planning news'}, {'quote':'Issuer is preparing a potential IPO.'}),[])

    def test_report_about_order_is_retained_but_not_original_order(self):
        need={'condition':'Official original court order staying the designation.'}
        self.assertIn('original_court_document_unverified',contract.issues(need,{'quote':'The court denied a stay.','document_context':'A news article about the court order.'}))
        self.assertEqual(contract.issues(need,{'quote':'The motion is denied.',
            'document_context':'UNITED STATES DISTRICT COURT. ORDER denying the motion.'}),[])
        self.assertEqual(contract.issues({'condition':'News reports about a court order'},
            {'quote':'The court denied a stay.'}),[])
        ordinary={'condition':'Existence of a court order that stays the designation.'}
        self.assertFalse(contract.contract(ordinary)['original_court_document_required'])
        self.assertIn('contrary_only_order_effect_witness',contract.issues(ordinary,{'quote':'The court denied the motion for a stay.'}))
        self.assertEqual(contract.issues(ordinary,{'quote':'The court granted an injunction staying the designation.'}),[])

    def test_every_need_must_be_accounted_for_without_inventing_binding(self):
        payload={'needs':[{'id':'date'},{'id':'publication'}],'passages':[{'passage_id':'P1'}]}
        positive={'need_id':'date','status':'proposed_binding','passage_ids':['P1'],'reason':'Date is printed in table.'}
        unknown={'need_id':'publication','status':'uncertain','passage_ids':['P1'],'reason':'Publication timestamp absent.'}
        bindings=[{'need_id':'date','passage_id':'P1'}]
        self.assertEqual(contract.validate_coverage(payload,[positive,unknown],bindings),[positive,unknown])
        for rows in ([positive],[positive,positive]):
            with self.assertRaises(ValueError):contract.validate_coverage(payload,rows,bindings)
        with self.assertRaisesRegex(ValueError,'binding_missing'):
            contract.validate_coverage(payload,[positive,unknown],[])
        with self.assertRaisesRegex(ValueError,'unknown_passage'):
            contract.validate_coverage(payload,[{**positive,'passage_ids':['invented']},unknown],bindings)
