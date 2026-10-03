"""Original preservation, observable screening, repair lineage and deterministic resume."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.evidence.collection_handoff import inspect,prepare,analysis_view,digest,load
from ForecastAgent.evidence.source_checks import candidate_guard


def bundle():
    body='Official announcement: the agency published its observation record. '*15
    return {'request':{'id':'7','question':'Will the agency publish a record?','question_type':'binary'},
        'pages':{'https://agency.gov/record':{'content':body},'https://mirror.org/record':{'content':body},
                 'https://news.org/login':{'content':'Log in\nPassword\nForgot password?\nCreate new account'}},
        'fetch_attempts':[{'url':u,'status':'completed'} for u in ('https://agency.gov/record','https://mirror.org/record','https://news.org/login')],
        'searches':[{'query':'record','status':'failed'}],'exa_searches':[],
        'model_attempts':[{'status':'reserved'}],'result':{'status':'collected','gaps':['Missing exact release date']}}


class HandoffTests(unittest.TestCase):
    def test_exact_duplicates_keep_all_aliases_and_original_ledgers(self):
        original=bundle();before=digest(original);view=analysis_view(original,before)
        self.assertEqual(len(view['pages']),1)
        self.assertEqual(view['source_aliases']['https://agency.gov/record'][0]['url'],'https://mirror.org/record')
        self.assertEqual(len(view['handoff_excluded_pages']),2)
        self.assertEqual(view['fetch_attempts'],original['fetch_attempts'])
        self.assertIn('Missing exact release date',view['gaps'])
        self.assertEqual(digest(original),before)
        modified=bundle();modified['pages']['https://mirror.org/record']['content']+='A corrected date.'
        self.assertEqual(len(analysis_view(modified,digest(modified))['pages']),2)

    def test_sec_sgml_filer_is_checked_and_unknown_is_not_guessed(self):
        request={'question':'Will Acme file an S-1 with the SEC?'}
        url='https://www.sec.gov/Archives/edgar/data/123/header.html'
        body='FILER:\n COMPANY DATA:\n  COMPANY CONFORMED NAME: ETF Opportunities Trust\n  CENTRAL INDEX KEY: 0000123\n'
        self.assertEqual(candidate_guard(request,{'url':url},body)['issuer_status'],'mismatch')
        self.assertEqual(candidate_guard(request,{'url':url},'An opaque filing with no issuer identifier.')['issuer_status'],'unknown')

    def test_quality_gap_does_not_manufacture_failed_physical_attempt(self):
        from ForecastAgent.runtime.gap_repair import inventory
        original=bundle()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);result=prepare(original,root)
            plan=inventory(root/'repair-parent.zip')
            self.assertEqual(plan['failed_physical_fetch_attempts'],0)
            self.assertEqual(plan['failed_fetches'][0]['kind'],'body_quality_gap')
            self.assertEqual(plan['failed_fetches'][0]['category'],'access_restricted')
            self.assertEqual(result['searches'],original['searches'])
            self.assertTrue((root/'raw-bundle.json').exists())

    def test_repair_handoff_is_rechecked_and_resume_does_not_repeat_it(self):
        original=bundle();calls=[]
        def repair(view,folder,ident):
            calls.append(ident);view=copy.deepcopy(view)
            view['pages']['https://news.org/login']={'content':'Short official notice: the record was published on May 15.'}
            return view
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);first=prepare(original,root,repair=repair);again=prepare(original,root,repair=repair)
            self.assertEqual(first,again);self.assertEqual(calls,['7'])
            self.assertEqual(len(first['pages']),2)
            self.assertEqual(digest(load(root/'raw-bundle.json')),digest(original))
            modified=bundle();modified['searches'].append({'query':'new'})
            with self.assertRaises(ValueError):prepare(modified,root,repair=repair)

    def test_repair_cannot_change_question_or_reserved_budget(self):
        def corrupt(view,*args):
            view['searches']=[];return view
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):prepare(bundle(),Path(d),repair=corrupt)

    def test_program_checks_reach_mercury_packet_without_analyzing(self):
        from ForecastAgent.analysis.referenced import evidence_packet
        b=bundle();view=analysis_view(b,digest(b));packet=evidence_packet(view)
        self.assertTrue(any('login' in str(g) for g in packet['acquisition_gaps']))
        self.assertEqual(len(packet['sources']),1)
        self.assertTrue(packet['collection_inspection']['exact_copies_are_not_independent_confirmation'])
        self.assertTrue(packet['collection_inspection']['source_aliases'])

    def test_existing_supplement_route_remains_default(self):
        from ForecastAgent.competition.live import supplement_bundle
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'FORECAST_CHECKED_HANDOFF':'0'}),patch('ForecastAgent.supplement.stage.run'),patch('ForecastAgent.supplement.stage.analysis_overlay',return_value=bundle()) as overlay:
            result=supplement_bundle(bundle(),Path(d),'7')
            overlay.assert_called_once();self.assertEqual(result,bundle())


if __name__=='__main__':unittest.main()
