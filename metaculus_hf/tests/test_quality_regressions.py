import copy,json,unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from pipeline_v2.structured_sources import registry_request,registry_observation
from pipeline_v2.review_periods import unsupported_series_assertion
from pipeline_v2.admission import rejection,assess,forecast_reading_reason
from pipeline_v2.acquisition import capture
from pipeline_v2.source_trace import primary_leads
from metaculus_agent import evidence_packing as packing
from ForecastAgent.competition import mercury as primitives
from ForecastAgent.analysis import mercury_evidence_chain as spans
from test_evidence_packing import source
import test_candidate_policy as candidates

SHELL='ClinicalTrials.gov\nShow glossary\nSearch for terms\nHide glossary\nStudy record managers: refer to the\nData Element Definitions\nif submitting registration or results information.'
URL='https://clinicaltrials.gov/search?viewType=Card&intr=LLM-based%20Chatbot&aggFilters=status:rec'
SERIES='SEAS YR ANOM\n'+''.join(f'{p}  2026  {v}\n' for p,v in [('DJF','0.1'),('JFM','0.2'),('FMA','0.4'),('MAM','0.8'),('AMJ','1.2'),('MJJ','1.3'),('JJA','1.5'),('JAS','1.69')])

class QualityTests(unittest.TestCase):
    def selected(self,value):
        packet=packing.prepare(primitives.packet_for(value),value)
        state,audit=packing.select(packet,spans.initial_state(packet),9830,spans.questions())
        for e in state['evidence']:self.assertEqual(e['text'],value['pages'][e['url']]['content'][e['start']:e['end']])
        return ''.join(e['text'] for e in state['evidence']),audit
    def test_designated_short_official_record_survives_long_competing_narrative(self):
        v=source('Example Alpha bill approved by Senate on October 20, 2026.\n',binding=False)
        v['request']['resolution_criteria']='Use official record https://example.org/data'
        page=copy.deepcopy(v['pages']['https://example.org/data']);page['content']='Unrelated NDA harassment discrimination employment reform.\n'*100
        import hashlib
        page['content_sha256']=hashlib.sha256(page['content'].encode()).hexdigest();page['capture_metadata']['text_sha256']=page['content_sha256'];v['pages']['https://example.org/nda']=page
        text,_=self.selected(v);self.assertIn('approved by Senate',text);self.assertNotIn('harassment',text)
    def test_all_existing_winner_records_keep_event_identity(self):
        text='Menu\n'+''.join(f'Round {i} Grand Prix\nWinner:\nDriver {i%7}\nRunner-up:\nOther driver\n' for i in range(1,19))
        v=source(text,binding=False);v['request']['question']='How many drivers will win a Grand Prix in 2026?'
        delivered,a=self.selected(v);self.assertEqual(delivered.count('Winner:'),18)
        for i in range(1,19):self.assertIn(f'Round {i} Grand Prix',delivered)
    def test_latest_series_preserves_labels_without_inventing_target_row(self):
        old=''.join(f'DJF  {y}  0.4\n' for y in range(1950,2026))
        v=source('SEAS YR ANOM\n'+old+SERIES.split('\n',1)[1],binding=False)
        v['request']['question']='Will ASO 2026 RONI exceed 2.0?'
        delivered,_=self.selected(v);self.assertIn('JAS  2026  1.69',delivered);self.assertNotIn('1950',delivered);self.assertNotIn('ASO  2026',delivered)
    def test_wrong_period_claim_is_rejected_but_future_gap_is_not(self):
        self.assertTrue(unsupported_series_assertion(SERIES,'Including the target ASO 2026 entry of 1.69.'))
        self.assertTrue(unsupported_series_assertion(SERIES,'JAS 2026 value is 2.2.'))
        self.assertFalse(unsupported_series_assertion(SERIES,'JAS 2026 entry of 1.69; ASO is unavailable.'))
        self.assertFalse(unsupported_series_assertion(SERIES,'No ASO 2026 entry of 1.69 is present.'))
    def test_bad_period_review_keeps_body_available_for_independent_reading(self):
        s=dict(source_id='s1',source_url='https://www.noaa.gov/data',status='extracted')
        p=dict(inclusion='supporting',content_kind='historical_data',forecast_relevance='baseline',source_role='historical_baseline',applicable_period='2026',
               coverage_note='Includes ASO 2026 entry of 1.69.',relevance=dict(entity='match',metric='match',period='match',quote='JAS  2026  1.69',as_of_date=None))
        b=assess(s,SERIES,p,require_kind=True);self.assertEqual(b['inclusion'],'pending');self.assertEqual(b['coverage_note'],'REVIEW_PERIOD_VALUE_MISMATCH')
        self.assertTrue(forecast_reading_reason(dict(source=s,text=SERIES,binding=b)))
        # Saved legacy approval must not discard the original body under a new guard.
        self.assertEqual(forecast_reading_reason(dict(source=s,text=SERIES,binding=p)),'REVIEW_PERIOD_VALUE_MISMATCH')
    def test_dynamic_shell_is_not_forecast_evidence(self):
        self.assertEqual(rejection(dict(source_url=URL,final_url=URL),SHELL),'EMPTY_PAGE_SHELL')
        self.assertIsNone(rejection(dict(source_url=URL,final_url=URL),'Clinical study NCT12345678: recruiting adults for chatbot intervention.'))
    def test_policy_link_is_not_a_primary_study_lead(self):
        d=dict(title='LLM-based Chatbot recruiting studies',resolution_source_urls=[URL])
        s=dict(source_id='s',status='extracted',source_url=URL,locator=dict(outgoing_links=[dict(url='https://clinicaltrials.gov/policy',title='Data Element Definitions')]))
        self.assertEqual(primary_leads(d,[s],[]),[])
    def test_registry_mapping_preserves_exact_intervention_and_recruiting(self):
        r=registry_request(URL);q=parse_qs(urlsplit(r['url']).query)
        self.assertEqual(q['query.intr'],['LLM-based Chatbot']);self.assertEqual(q['filter.overallStatus'],['RECRUITING']);self.assertEqual(q['countTotal'],['true'])
        for url in [URL+'&cond=cancer',URL+'&intr=other',URL.replace('status:rec','status:rec,act'),'https://clinicaltrials.gov.evil.test/search?intr=a&aggFilters=status:rec']:
            self.assertIsNone(registry_request(url))
    def test_registry_full_raw_response_and_partial_page_count_remain_distinct(self):
        payload={'studies':[{'protocolSection':{'identificationModule':{'nctId':'NCT12345678'},'statusModule':{'overallStatus':'RECRUITING'}}}],'totalCount':23,'nextPageToken':'next'}
        raw=json.dumps(payload).encode();r=registry_request(URL)
        class Transport:
            def request(self,*a,**kw):return raw,dict(final_url=r['url'],requested_at='2026-10-28T00:00:00Z',captured_at='2026-10-28T00:00:01Z',http_status=200)
        with patch('pipeline_v2.acquisition.public_url'):
            obs,text=registry_observation(Transport(),URL,r)
        self.assertEqual(text.encode(),raw);self.assertFalse(obs['locator']['coverage']['record_page_complete']);self.assertEqual(obs['locator']['coverage']['reported_total'],23)
    def test_registry_inconsistent_empty_result_never_becomes_zero_baseline(self):
        r=registry_request(URL)
        class Transport:
            def request(self,*a,**kw):return b'{"studies":[],"totalCount":5}',dict(final_url=r['url'],requested_at='x',captured_at='y',http_status=200)
        with patch('pipeline_v2.acquisition.public_url'),self.assertRaisesRegex(Exception,'REGISTRY_COUNT_INCONSISTENT'):
            registry_observation(Transport(),URL,r)
    def test_registry_wrong_status_and_duplicate_ids_are_not_accepted(self):
        r=registry_request(URL)
        study={'protocolSection':{'identificationModule':{'nctId':'NCT12345678'},'statusModule':{'overallStatus':'COMPLETED'}}}
        rows=[study]
        class Transport:
            def request(self,*a,**kw):return json.dumps(dict(studies=rows,totalCount=len(rows))).encode(),dict(final_url=r['url'],requested_at='x',captured_at='y',http_status=200)
        with patch('pipeline_v2.acquisition.public_url'),self.assertRaisesRegex(Exception,'REGISTRY_STATUS_MISMATCH'):
            registry_observation(Transport(),URL,r)
        study['protocolSection']['statusModule']['overallStatus']='RECRUITING';rows.append(copy.deepcopy(study))
        with patch('pipeline_v2.acquisition.public_url'),self.assertRaisesRegex(Exception,'REGISTRY_STUDY_IDENTITY'):
            registry_observation(Transport(),URL,r)

class ClosingRecoveryTests(unittest.TestCase):
    prepare=candidates.CandidatePolicyTests.prepare
    tools=candidates.CandidatePolicyTests.tools
    def test_failed_earlier_handoff_does_not_block_final_saved_candidate(self):
        from pipeline_v2.contracts import PipelineError
        engine,qid,rid,d=self.prepare(background=False);tools=self.tools(d)
        def planner(_):raise PipelineError('HTTP_502')
        def reviewer(state):return dict(action='finish',materials=[dict(source_id=m['source_id'],content_kind='reference_index',inclusion='excluded') for m in state['fetched_materials']])
        sid=capture(engine,qid,rid,tools=tools,planner=planner,reviewer=reviewer)
        snap=engine.repo.read_snapshot(qid,sid)
        self.assertEqual(len([u for k,u in tools.events if k=='page']),4)
        self.assertEqual(snap['context']['planner_handoff_page_attempts'],2)
        self.assertIn('CLOSING_DETAIL_HANDOFF',{g['code'] for g in snap['gaps']})

if __name__=='__main__':unittest.main()
