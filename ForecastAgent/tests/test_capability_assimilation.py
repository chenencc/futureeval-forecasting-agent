"""Reference-first defaults and original-only fallback for both arm orders."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.analysis.pilot import load,save
from ForecastAgent.evals import cases,campaign
from ForecastAgent.research_loop import brief_trial,forecast_brief_refs as refs,forecast_brief
from ForecastAgent.tests.test_research_forecast_brief import table_bundle
from ForecastAgent.tests.test_competition_mercury import response


class CapabilityAssimilationTests(unittest.TestCase):
    def test_unknown_may_cite_literal_reporting_gap_without_establishing_no(self):
        case,gold=cases.fixture('person_role',3)
        result={'answer':'unknown','source_id':'S1','basis':'unknown',
                'quote':'The Energy appointment has not been identified in this report.'}
        grade=campaign.grade(case,gold,{'model':'super'},result)
        self.assertTrue(grade['answer_correct']);self.assertTrue(grade['source_correct'])
        self.assertTrue(grade['literal_quote_valid'])
        result['answer']='no'
        self.assertFalse(campaign.grade(case,gold,{'model':'super'},result)['answer_correct'])

    def test_new_fixture_asks_event_status_not_report_entailment(self):
        case,gold=cases.fixture('missing_evidence',0)
        self.assertNotIn('establish that',case['question'])
        self.assertIn('attendance status',case['question'])
        self.assertEqual(gold['answer'],'unknown')
        self.assertIn('did not attend',case['options'])

    def test_failed_brief_reuses_original_for_both_orders_and_all_contract_failures(self):
        for error in ('long note exceeds cap','array must be array','unknown source binding'):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}):
                folder=Path(temp);parents=[]
                for ident in ('1','2'):
                    bundle=table_bundle();bundle['request']['id']=ident
                    path=folder/(ident+'.json');save(path,bundle);parents.append(path)
                def decision(state,heads,key,observer,**kwargs):
                    token=observer('reserve',{'status':'reserved','request':{'model':'mercury'}})
                    value=response(heads);observer('complete',{'status':'received','response':value},token)
                    return value
                with patch.object(refs,'brief_stage',side_effect=ValueError(error)),patch.object(refs.decision_http,'decide',side_effect=decision) as transport:
                    result=brief_trial.run(parents,folder/'trial',execute=True,max_http=6)
                self.assertEqual(transport.call_count,2)
                self.assertEqual(result['completed'],2);self.assertEqual(result['paired_completed'],0)
                self.assertEqual(result['new_http'],2)
                self.assertEqual(result['brief_protocol'],refs.PROTOCOL)
                for row in result['cases']:
                    arm=row['arms']['brief']
                    self.assertTrue(arm['reused_original_response'])
                    self.assertFalse(arm['independent_comparison_arm'])
                    self.assertEqual(arm['summary'],row['arms']['direct']['summary'])
                    self.assertEqual(arm['original_state_sha256'],row['arms']['direct']['original_state_sha256'])
                with patch.object(refs,'brief_stage',side_effect=ValueError(error)),patch.object(refs.decision_http,'decide',side_effect=AssertionError('Unexpected network')):
                    replay=brief_trial.run(parents,folder/'trial',execute=True,max_http=6)
                self.assertEqual(result,replay)

if __name__=='__main__':unittest.main()
