"""Validate gold isolation, paired material, real citation and retry boundaries."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.evals import cases, campaign
from ForecastAgent.runtime.records import load, save


class CapabilityCampaignTests(unittest.TestCase):
    def test_fixture_gold_is_deterministic_and_unknown_is_not_negative(self):
        first=cases.controlled();second=cases.controlled()
        self.assertEqual(first,second)
        self.assertEqual(len(first[2]),384)
        for case_id,case in first[0].items():
            gold=first[1][case_id]
            self.assertIn(gold['answer'],case['options'])
            if gold['source_id']=='S1':
                original=next(s['text'] for s in case['sources'] if s['id']=='S1')
                self.assertIn(gold['quote'],original)
            if gold['source_id']=='NONE':self.assertEqual(gold['quote'],'')
            if gold['basis']=='unknown':self.assertEqual(gold['answer'],'unknown')

    def test_gold_never_enters_transport_and_prompt_arms_have_identical_materials(self):
        fixture,gold=cases.fixture('rule_condition',6)
        for model in cases.MODELS:
            a=cases.classification_payload(fixture,{'model':model,'prompt':'P0'})
            b=cases.classification_payload(fixture,{'model':model,'prompt':'P1'})
            def state(payload):
                return payload['state'] if model=='mercury' else json.loads(payload['messages'][1]['content'])
            for field in ('question','rules','sources'):
                self.assertEqual(state(a)[field],state(b)[field])
            self.assertNotIn('gold',json.dumps(a))
            self.assertNotIn('validation',json.dumps(a))

    def test_quote_binding_is_separate_from_answer_correctness(self):
        fixture,gold=cases.fixture('event_time',0)
        answer={**gold,'quote':'invented quote'}
        result=campaign.grade(fixture,gold,{'model':'super'},answer)
        self.assertTrue(result['answer_correct'])
        self.assertFalse(result['literal_quote_valid'])

    def test_brief_does_not_change_originals_or_forecast_head(self):
        common={'question':{'question':'Will X?'},'evidence':[{'text':'A source.'}]}
        a=campaign.forecast_payload(common)
        b=campaign.forecast_payload(common,{'observations':[],'conditions':[],'unknowns':[]})
        self.assertEqual(a['questions'],b['questions'])
        for key,value in a['state'].items():self.assertEqual(b['state'][key],value)
        self.assertNotIn('forecast_brief',common)

    def test_unbound_brief_cannot_reach_decision(self):
        common={'evidence':[{'evidence_id':'R1','text':'An exact observation.'}]}
        payload=campaign.brief_payload(common)
        reply={'model':cases.MODELS['super'],'choices':[{'finish_reason':'tool_calls','message':{
            'tool_calls':[{'function':{'name':'record_brief','arguments':json.dumps({
                'observations':[{'evidence_id':'R1','quote':'invented','interpretation':'guess'}],
                'conditions':[],'unknowns':[]})}}]}}]}
        with self.assertRaisesRegex(ValueError,'not exactly bound'):
            campaign.parse(reply,payload,{'model':'super','kind':'brief'})

    def test_response_truncation_is_not_semantic_failure(self):
        fixture,_=cases.fixture('event_time',0)
        payload=cases.classification_payload(fixture,{'model':'super','prompt':'P0'})
        with self.assertRaisesRegex(ValueError,'Completion budget exhausted'):
            campaign.parse({'model':cases.MODELS['super'],'choices':[{'finish_reason':'length'}]},payload,{'model':'super','kind':'decision'})

    def test_frozen_identity_refuses_silent_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'identity.json'
            campaign.immutable(path,{'cap':500})
            with self.assertRaisesRegex(ValueError,'Frozen identity changed'):
                campaign.immutable(path,{'cap':1000})
            self.assertEqual(load(path),{'cap':500})

    def test_physical_cap_stops_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=object.__new__(campaign.Runner)
            runner.http=Path(folder);runner.mutex=campaign.threading.RLock()
            runner.halt=None;runner.next_request=0;runner.quota_guard=None
            for index in range(500):save(runner.http/f'{index:04}.json',{'retry':False})
            with patch('ForecastAgent.evals.campaign.urlopen') as network:
                self.assertIsNone(runner.request({}, {},False))
                network.assert_not_called()


if __name__=='__main__':unittest.main()
