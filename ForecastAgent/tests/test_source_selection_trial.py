"""Acquisition-only selection replay, provider bounds and fresh arm preservation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import digest,load
from ForecastAgent.source_selection_trial import freeze_candidates,selection_question,capture_arm
from ForecastAgent.providers import source_selection


def parent():
    return {'request':{'id':'7','question':'Did Agency A report 10 units by May 2026?',
        'resolution_criteria':'Use the May 2026 INITIAL report.','background':'https://example.org/official'},
        'plan':[{'id':'N1','priority':'critical','condition':'Exact target release','expected_source':'Official report','query':'Agency A report May 2026'}],
        'searches':[{'results':[{'url':'https://example.org/one','title':'Official release','content':'Snippet lead'},{'url':'https://example.org/two','title':'Other topic','content':'Another lead'}]}],
        'pages':{'https://example.org/one':{'content':'SENTINEL_SECRET_OUTCOME'}},
        'fetch_attempts':[{'url':'https://example.org/one','status':'completed'}]}


class SelectionTests(unittest.TestCase):
    def test_blind_candidate_packet_and_exact_old_order(self):
        b=parent();c,old=freeze_candidates(b)
        self.assertEqual(old,['https://example.org/one'])
        self.assertNotIn('SENTINEL_SECRET_OUTCOME',json.dumps(c)+json.dumps(selection_question(b)))
        self.assertTrue(all(c['candidate_id'] for c in c))

    def test_selection_is_bounded_cached_and_not_outcome_scoring(self):
        candidates,_=freeze_candidates(parent());calls=[]
        def call(state,folder,registry):
            self.assertLessEqual(source_selection.request_bytes(state,registry),source_selection.MAX_REQUEST_BYTES)
            self.assertFalse(any(k.startswith('event_') for k in registry))
            answers={}
            for key,q in registry.items():
                if q['type']=='score':answers[key]={'score':3.,'probabilities':{'0':0.,'1':0.,'2':0.,'3':1.}}
                else:
                    chosen=next(iter(q['criteria']));answers[key]={'choice':chosen,'probabilities':{k:float(k==chosen) for k in q['criteria']}}
            calls.append(1);return {'answers':answers}
        with tempfile.TemporaryDirectory() as d,patch.object(source_selection,'call',call):
            result=source_selection.select(selection_question(parent()),candidates,Path(d),1)
            again=source_selection.select(selection_question(parent()),candidates,Path(d),1)
            self.assertEqual(result,again);self.assertEqual(len(calls),1)
            self.assertEqual(len(result['selected_urls']),1)

    def test_fresh_arms_use_same_reader_and_resume_without_calls(self):
        b=parent();before=digest(b);c,old=freeze_candidates(b)
        def fetch(*args,**kwargs):return {'content':'Official target period source data. '*35,'links':[],'updated_at':None}
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'FORECAST_SHARED_CACHE_ROOT':''}),patch('ForecastAgent.runtime.retrieval.fetch_public_page',side_effect=fetch) as reader,patch('ForecastAgent.runtime.retrieval.fetch_structured',return_value=None):
            folder=Path(d)
            for arm in ['A','B']:
                first=capture_arm(b,c,old,folder/arm,arm,'identity')
                again=capture_arm(b,c,old,folder/arm,arm,'identity')
                self.assertEqual(first,again)
                self.assertEqual(first['physical_fetch_attempts'],1)
                self.assertEqual(first['tavily_attempts'],0)
            self.assertEqual(reader.call_count,2)
        self.assertEqual(digest(b),before)


if __name__=='__main__':unittest.main()
