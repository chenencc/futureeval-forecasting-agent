import copy,hashlib,unittest
from pipeline_v2.research_query import validate,fallback,ResearchQuery
from pipeline_v2.research_design import reading_candidate
from pipeline_v2.acquisition import capture
from pipeline_v2.contracts import PipelineError
from metaculus_agent import evidence_packing as packing
from ForecastAgent.competition import mercury as primitives
from ForecastAgent.analysis import mercury_evidence_chain as spans
from test_evidence_packing import source
import test_research_query as research

class DesignTests(unittest.TestCase):
    def state(self):return dict(question=dict(title='How much digital advertising revenue will Example Company report in Q3 2026?',resolution_source_urls=[]),base_searches=[],base_candidates=[])
    def plan(self,q):return dict(query=q,research_goal='Find prior Q1 and Q2 2026 digital advertising revenue.',why_this_query='Historical quarterly baseline is missing.',evidence_kind='historical_baseline')
    def test_goal_seeking_prior_quarters_cannot_send_only_target_quarter(self):
        with self.assertRaisesRegex(PipelineError,'GOAL_PERIOD_MISMATCH'):
            validate(self.plan('Example Company Q3 2026 digital advertising revenue earnings'),self.state())
    def test_explicit_previous_quarter_year_or_seasonality_are_valid(self):
        for q in ['Example Company Q1 Q2 2026 digital advertising revenue','Example Company Q3 2025 digital advertising revenue','Example Company Q3 2026 historical advertising revenue seasonality']:
            self.assertEqual(validate(self.plan(q),self.state())['query'],q)
    def test_degraded_query_retains_metric_and_targets_prior_period(self):
        q=fallback(self.state())['query'];self.assertIn('Q1 Q2 2026',q);self.assertNotIn('Q3 2026',q);self.assertIn('digital advertising revenue',q)
    def test_unrecognized_period_is_not_invented(self):
        s=self.state();s['question']['title']='Will Example Company develop a new product?';self.assertNotIn('Q1',fallback(s)['query'])
    def test_research_reader_prefers_new_official_document_and_excludes_base_and_social(self):
        definition=dict(title='Example Company digital advertising revenue Q3 2026',resolution_source_urls=['https://example.org/press'])
        row=lambda u:dict(url=u,title='Example Company digital advertising revenue Q2 2026 earnings report',snippet='')
        base=dict(provider='exa',phase='acquisition',results=[row('https://example.org/old')])
        rs=dict(provider='tavily',phase='acquisition',agent_visible_extras={'research_query_role':'mandatory_research'},results=[row('https://youtube.com/watch?v=1'),row('https://example.org/old'),row('https://example.org/press-release/q2')])
        self.assertEqual(reading_candidate(definition,[base,rs],[],[]),'https://example.org/press-release/q2')
        self.assertIsNone(reading_candidate(definition,[base,rs],['https://example.org/press-release/q2'],[]))

class FollowthroughTests(unittest.TestCase):
    def setUp(self):
        self.fixture=research.ResearchQueryTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
    def test_one_new_research_body_is_read_within_existing_page_limit(self):
        class Tools(research.Tools):
            def result(inner,provider,query,phase):
                r=super().result(provider,query,phase)
                if query==research.QUERY['query']:r['results']=[dict(url='https://example.org/report/baseline',title='Example Alpha historical measurement report',snippet='Example Alpha counts',provider_score=None,published_at=None)]
                return r
        f=self.fixture;tools=Tools()
        sid=f.run_capture(tools);snapshot=f.engine.repo.read_snapshot(f.qid,sid)
        self.assertEqual(sum(u=='https://example.org/report/baseline' for k,u in tools.events if k=='page'),1)
        self.assertIn('RESEARCH_CANDIDATE_FOLLOWTHROUGH',{g['code'] for g in snapshot['gaps']})
        self.assertEqual(len(f.engine.repo.get_question(f.qid)['searches']),4)
    def test_query_model_inputs_exclude_platform_forecast_candidates(self):
        f=self.fixture;s=dict(provider='exa',phase='acquisition',query='base',status='ok',results=[dict(url='https://www.metaculus.com/questions/123',title='Community prediction'),dict(url='https://example.org/report',title='Independent report')])
        session=ResearchQuery(f.root,f.qid,f.rid,f.definition,[s]);self.assertEqual(len(session.input['base_candidates']),1)

class FocusTests(unittest.TestCase):
    def select(self,v):
        p=packing.prepare(primitives.packet_for(v),v);return packing.select(p,spans.initial_state(p),9830,spans.questions())
    def test_recent_quarterly_baseline_outranks_old_unbound_company_history(self):
        v=source('Example Company delivered 480000 vehicles in Q2 2026.\n',binding=False)
        v['request']['question']='How many vehicles will Example Company deliver in Q4 2026?'
        v['request']['resolution_criteria']='Use https://example.org/data'
        old=copy.deepcopy(next(iter(v['pages'].values())));old['content']='Example Company delivered 109 vehicles in 2009 after its early funding round.\n';old['content_sha256']=hashlib.sha256(old['content'].encode()).hexdigest();old['capture_metadata']['text_sha256']=old['content_sha256'];v['pages']['https://example.org/history']=old
        state,audit=self.select(v);text=''.join(e['text'] for e in state['evidence'])
        self.assertIn('480000',text);self.assertNotIn('funding',text);self.assertTrue(audit['low_focus_deferred'])
    def test_only_old_body_remains_independently_readable(self):
        v=source('Example Company delivered 109 vehicles in 2009 after its early funding round.\n',binding=False);v['request']['question']='How many vehicles will Example Company deliver in Q4 2026?'
        state,audit=self.select(v);self.assertTrue(state['evidence']);self.assertTrue(audit['weak_only_fallback'])
    def test_reviewed_driver_is_not_dropped_for_missing_quarterly_metric(self):
        v=source('Example Company factory closed due to a severe earthquake on October 20, 2026.\n',binding=False);v['request']['question']='How many vehicles will Example Company deliver in Q4 2026?'
        v['source_bindings']['https://example.org/data']=dict(inclusion='supporting',forecast_relevance='driver',source_role='background',relevance=dict(span=dict(start=0,end=len(next(iter(v['pages'].values()))['content']))))
        other=copy.deepcopy(v['pages']['https://example.org/data']);other['content']='Example Company delivered 500000 vehicles in Q3 2026.\n';other['content_sha256']=hashlib.sha256(other['content'].encode()).hexdigest();other['capture_metadata']['text_sha256']=other['content_sha256'];v['pages']['https://example.org/current']=other
        state,audit=self.select(v);self.assertIn('earthquake',''.join(e['text'] for e in state['evidence']))

class FailureRegressionTests(unittest.TestCase):
    def test_malformed_url_and_known_path_variant_lose_to_distinct_document(self):
        d=dict(title='Example Company digital advertising revenue Q3 2026',resolution_source_urls=['https://example.org/q1.pdf'])
        row=lambda u:dict(url=u,title='Example Company digital advertising revenue earnings report',snippet='')
        rs=dict(provider='exa',phase='acquisition',agent_visible_extras={'research_query_role':'mandatory_research'},results=[row('https://example.org/report/https:/example.org/q2'),row('https://example.org/q1.pdf?format=print'),row('https://example.org/q2.pdf')])
        self.assertEqual(reading_candidate(d,[rs],['https://example.org/q1.pdf'],[]),'https://example.org/q2.pdf')
        rs['results']=rs['results'][:2]
        self.assertEqual(reading_candidate(d,[rs],['https://example.org/q1.pdf'],[]),'https://example.org/q1.pdf?format=print')
    def test_invalid_reviewer_date_allows_body_only_future_date_still_blocked(self):
        from pipeline_v2.admission import original_read_view
        m=dict(source=dict(source_id='s1',status='extracted',source_url='https://example.org/report'),text='Independent original factual body with a reported observed event.',binding=dict(source_id='s1',source_role='unresolved',inclusion='pending',content_kind='factual_report',forecast_relevance='direct',coverage_note='INVALID_AS_OF_DATE',relevance=dict(as_of_date='Oct someday',span={'start':0,'end':22})))
        snap=dict(materials=[m],context={},evidence_cutoff_at='2026-10-28T00:00:00Z');before=copy.deepcopy(snap)
        v=original_read_view(snap);self.assertEqual(v['materials'][0]['text'],m['text']);self.assertNotIn('relevance',v['materials'][0]['binding']);self.assertFalse(v['materials'][0]['decision_reading']['truth_verified']);self.assertEqual(snap,before)
        snap['materials'][0]['binding']['coverage_note']='FUTURE_AS_OF_DATE';self.assertFalse(original_read_view(snap)['materials'])
    def test_designated_metric_body_not_suppressed_by_wrong_context_label(self):
        v=source('Example Company delivered 480000 vehicles in Q2 2026.\n',binding=False);v['request']['question']='How many vehicles will Example Company deliver in Q4 2026?';v['request']['resolution_criteria']='Use https://example.org/data'
        v['source_bindings']['https://example.org/data']=dict(inclusion='supporting',forecast_relevance='context',relevance=dict(metric='mismatch'))
        p=packing.prepare(primitives.packet_for(v),v);state,audit=packing.select(p,spans.initial_state(p),9830,spans.questions());self.assertIn('480000',''.join(e['text'] for e in state['evidence']));self.assertFalse(audit['weak_only_fallback'])

class MetricPackingTests(unittest.TestCase):
    def select(self,v):
        p=packing.prepare(primitives.packet_for(v),v);return packing.select(p,spans.initial_state(p),9830,spans.questions())
    def test_numeric_delivery_cells_keep_metric_header_and_blank_separated_period(self):
        v=source('Example Company Fourth Quarter 2025 Results\n\nQ4 2025\n\n| Model | Production | Deliveries |\n| --- | --- | --- |\n| Total | 434358 | 418227 |\n',binding=False);v['request']['question']='How many vehicles will Example Company deliver in Q4 2026?'
        state,audit=self.select(v);text=''.join(e['text'] for e in state['evidence'])
        for x in ['Q4 2025','Production | Deliveries','418227']:self.assertIn(x,text)
        self.assertGreater(audit['table_rows_selected'],0)
    def test_wrapped_metric_paragraph_keeps_amount_and_period_without_other_tables(self):
        body='Example Company Third Quarter 2025 Results\nNovember 5, 2025\n'+('Unrelated general corporate background. '*30)+'\nAdvertising Revenues\nTotal advertising revenues were $132.3 million in the third quarter of 2025. Digital\nadvertising revenues increased 20.3 percent to $98.1 million, due to strong demand.\nPrint advertising revenues decreased.\n'
        v=source(body,binding=False);v['request']['question']='How much digital advertising revenue will Example Company report in Q3 2026?'
        state,audit=self.select(v);text=''.join(e['text'] for e in state['evidence'])
        for x in ['$98.1 million','third quarter of 2025']:self.assertIn(x,text)
        for e in state['evidence']:self.assertEqual(e['text'],body[e['start']:e['end']])

if __name__=='__main__':unittest.main()
