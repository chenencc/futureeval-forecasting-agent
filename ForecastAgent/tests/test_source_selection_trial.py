"""Acquisition-only selection replay, provider bounds and fresh arm preservation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import digest,load
from ForecastAgent.source_selection_trial import freeze_candidates,selection_question,capture_arm,run,IDS
from ForecastAgent.providers import source_selection
from ForecastAgent.providers.source_selection_guards import candidate_guard,inspect_body,screen_bundle


def parent():
    return {'request':{'id':'7','question':'Did Agency A report 10 units by May 2026?',
        'resolution_criteria':'Use the May 2026 INITIAL report.','background':'https://example.org/official'},
        'plan':[{'id':'N1','priority':'critical','condition':'Exact target release','expected_source':'Official report','query':'Agency A report May 2026'}],
        'searches':[{'results':[{'url':'https://example.org/one','title':'Official release','content':'Snippet lead'},{'url':'https://example.org/two','title':'Other topic','content':'Another lead'}]}],
        'pages':{'https://example.org/one':{'content':'SENTINEL_SECRET_OUTCOME'}},
        'fetch_attempts':[{'url':'https://example.org/one','status':'completed'}]}


class SelectionTests(unittest.TestCase):
    def test_rule_source_survives_low_rank_without_exceeding_budget(self):
        q={'question':'Will Acme file an S-1 with the SEC?', 'resolution_criteria':'Use https://www.sec.gov/search-filings; definition https://www.investopedia.com/terms/s/sec-form-s-1.asp'}
        candidates=[{'candidate_id':'a','url':'https://www.sec.gov/search-filings'},
                    {'candidate_id':'b','url':'https://news.org/b'},
                    {'candidate_id':'c','url':'https://www.investopedia.com/terms/s/sec-form-s-1.asp'}]
        def call(state,folder,registry):
            return {'answers':{k:{'score':0 if k=='a_priority' else 3,
                'probabilities':{'0':1 if k=='a_priority' else 0}} for k in registry}}
        with tempfile.TemporaryDirectory() as d,patch.object(source_selection,'call',call):
            r=source_selection.select(q,candidates,Path(d),1)
            self.assertEqual(r['selected_ids'],['a'])
            self.assertEqual(len(r['selected_urls']),1)
        q['resolution_criteria']+=' https://news.org/b'
        with tempfile.TemporaryDirectory() as d,patch.object(source_selection,'call',call):
            r=source_selection.select(q,candidates,Path(d),1)
            self.assertEqual(len(r['rule_source_overflow_ids']),1)

    def test_issuer_checks_unknown_path_aliases_and_explicit_conflict(self):
        q={'question':'Will Acme file an S-1 with the SEC?'}
        url='https://www.sec.gov/Archives/edgar/data/123/filing.htm'
        self.assertEqual(candidate_guard(q,{'url':url})['issuer_status'],'unknown')
        self.assertFalse(inspect_body(q,url,'Other Energy Corp (Filer)\n'+('Registration filing details. '*15))['eligible_for_evidence'])
        q['target_issuer']={'name':'Acme','cik':'000123','aliases':['Acme Research LLC']}
        self.assertEqual(candidate_guard(q,{'url':url})['issuer_status'],'match')
        self.assertEqual(candidate_guard(q,{'url':url.replace('/123/','/456/')})['issuer_status'],'mismatch')
        del q['target_issuer']['cik']
        self.assertEqual(candidate_guard(q,{'url':url,'issuer_name':'Acme Research LLC'})['issuer_status'],'match')
        del q['target_issuer'];q['resolution_criteria']='Acme or any subsidiary involved in this product.'
        self.assertEqual(candidate_guard(q,{'url':url,'issuer_name':'Other Corp'})['issuer_status'],'unverified_affiliate')

    def test_shells_excluded_with_raw_preservation_and_short_records_retained(self):
        nav='Hoppa í aðalefni\nFréttir\nÚtvarp\nSjónvarp\nMeira\nEnglish\nPolski\nLeita á síðunni\nValmynd'
        login='Log into Facebook\nEmail or mobile number\nPassword\nLog in\nForgot password?\nCreate new account'
        for text in [nav,login]:self.assertFalse(inspect_body({},'https://news.org',text)['eligible_for_evidence'])
        self.assertTrue(inspect_body({},'https://agency.gov','May 15, 2026: The Board named the incumbent chair pro tempore.')['eligible_for_evidence'])
        table='Search\nResults\nMenu\n| Candidate | Votes |\n| A | 150 |\n| B | 170 |'
        self.assertTrue(inspect_body({},'https://agency.gov',table)['eligible_for_evidence'])
        original={'request':{},'pages':{'https://news.org':{'content':nav}}}; before=digest(original)
        screened=screen_bundle(original)
        self.assertEqual(screened['pages'],{})
        self.assertEqual(screened['selection_excluded_pages']['https://news.org']['content'],nav)
        self.assertTrue(screened['result']['gaps'])
        self.assertEqual(digest(original),before)

    def test_compact_batches_have_one_head_per_candidate_and_keep_entire_pool(self):
        candidates=[{'candidate_id':str(i),'url':'https://example.org/'+str(i),
                     'snippet':'A promising discovery lead. '*50,'title':'Original record'} for i in range(41)]
        packets=list(source_selection.batches({'question':'Does the event occur?'},candidates))
        self.assertLessEqual(len(packets),2)
        self.assertEqual(sum(len(chosen) for chosen,_,_ in packets),41)
        for chosen,state,registry in packets:
            self.assertEqual(len(registry),len(chosen))
            self.assertLessEqual(source_selection.request_bytes(state,registry),28000)
            self.assertTrue(all(len(c['snippet'])<=160 for c in state['candidates']))

    def test_full_batch_creates_output_before_lock_and_preserves_resume(self):
        with tempfile.TemporaryDirectory() as d:
            from ForecastAgent.analysis.pilot import save
            root=Path(d)
            for ident in IDS[:5]:
                b=parent();b['request']['id']=ident
                save(root/'inputs/tasks'/ident/'bundle.json',b)
            def selector(question,candidates,folder,limit):
                return {'selected_urls':['https://example.org/one'],'physical_http_batches':1}
            def fetch(*args,**kwargs):return {'content':'Official target period source data. '*35,'links':[],'updated_at':None}
            with patch('ForecastAgent.source_selection_trial.select',selector),patch.dict('os.environ',{'FORECAST_SHARED_CACHE_ROOT':''}),patch('ForecastAgent.runtime.retrieval.fetch_public_page',side_effect=fetch) as reader,patch('ForecastAgent.runtime.retrieval.fetch_structured',return_value=None):
                run(root/'inputs',root/'new/output',1)
                run(root/'inputs',root/'new/output',1)
                self.assertEqual(reader.call_count,10)
            self.assertTrue(all(r['status']=='completed' for r in load(root/'new/output/report.json')['rows']))

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
