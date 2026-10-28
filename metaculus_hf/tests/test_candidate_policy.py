import copy,json,tempfile,unittest
from pathlib import Path
from pipeline_v2.acquisition import capture
from pipeline_v2.admission import rejection,forecast_reading_reason,assess
from pipeline_v2.candidate_policy import platform_exclusion,topic_match,background_links
from pipeline_v2.retrieval_plan import recovery_candidates,candidate_exclusion,QUERY_POLICY,seed_plan
from pipeline_v2.source_trace import primary_leads
from pipeline_v2.engine import Engine,collection_profile
from pipeline_v2.repository import Repository
from test_pipeline_v2_initial_search import Tools

FAQ='https://www.metaculus.com/faq/#definitions'
BACKGROUND='https://sports.example.org/grand-slam-of-darts-format'
MIRROR='https://forecast.example.org/metaculus/432'
WRONG='https://www.metaculus.com/questions/8579/us-freedom-report/'
TITLE='How many nine-dart finishes in the 2026 Grand Slam of Darts?'
DEF=dict(title=TITLE,resolution_criteria='Count nine-dart finishes using credible sources: '+FAQ,resolution_source_urls=[FAQ],description='Background: [Grand Slam of Darts format]('+BACKGROUND+')')

class CandidatePolicyTests(unittest.TestCase):
    def prepare(self,background=True):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);engine=Engine(Repository(tmp.name));qid='metaculus:fixture'
        fixture=json.loads((Path(__file__).resolve().parents[1]/'fixtures/question_jsonl/binary.jsonl').read_text());definition=copy.deepcopy(next(iter(fixture['question']['versions'].values()))['definition']);definition.update(DEF)
        if not background:definition['description']=''
        engine.question(qid,'https://www.metaculus.com/questions/fixture/',definition);rid=engine.new_run(qid,collection_profile(offline=True))
        return engine,qid,rid,definition
    def tools(self,definition):
        class SavedTools(Tools):
            def result(inner,provider,query,phase):
                result=super().result(provider,query,phase)
                result['results']=[dict(url=MIRROR,title=TITLE+' Metaculus Scans',snippet=TITLE,published_at=None,provider_score=None)]+[
                    dict(url='https://sports.example.org/grand-slam-of-darts-history-'+str(i),title='Grand Slam of Darts historical nine-dart finishes '+str(i),snippet=TITLE,published_at=None,provider_score=None) for i in range(4)]
                return result
            def page(inner,url):
                inner.events.append(('page',url));observation,text=inner.body(url)
                return observation,'Synthetic fixture only, not live sports evidence: Grand Slam of Darts historical nine-dart finishes and match format.'
        return SavedTools()
    def test_faq_and_platform_forecasts_are_hard_excluded_even_if_model_would_approve(self):
        for url,expected in [(FAQ,'PLATFORM_RULE_REFERENCE'),(WRONG,'PLATFORM_FORECAST_PAGE'),(MIRROR,'PREDICTION_QUESTION_MIRROR')]:
            s=dict(source_id='s1',source_url=url,status='extracted');text='Example readable page, not independent event information.'
            self.assertEqual(platform_exclusion(url),expected);self.assertEqual(rejection(s,text),expected)
            self.assertEqual(assess(s,text,dict(inclusion='core'))['inclusion'],'excluded')
            self.assertIsNone(forecast_reading_reason(dict(source=s,text=text,binding=dict(inclusion='pending'))))
    def test_faq_cannot_turn_its_unrelated_outgoing_links_into_primary_leads(self):
        source=dict(source_id='s1',source_url=FAQ,status='extracted',locator=dict(outgoing_links=[dict(url=WRONG,title='US Freedom in the World Report data')]))
        self.assertEqual(primary_leads(DEF,[source],[]),[])
    def test_unrelated_same_domain_government_reports_do_not_override_topic(self):
        d={**DEF,'resolution_source_urls':['https://sports.gov/records']}
        source=dict(source_id='s1',source_url='https://sports.gov/records',status='extracted',locator=dict(outgoing_links=[dict(url='https://sports.gov/cpi-data',title='Annual inflation report data')]))
        self.assertEqual(primary_leads(d,[source],[]),[])
    def test_exact_legal_document_identifiers_still_qualify_without_repeating_full_title(self):
        d=dict(title='Will Hochul sign the bill?',resolution_criteria='Bill A11560',resolution_source_urls=['https://assembly.state.ny.us/leg/'])
        link=dict(url='https://assembly.state.ny.us/leg/?bn=A11560',title='Official status')
        s=dict(source_id='s1',source_url='https://news.example.org/story',status='extracted',locator=dict(outgoing_links=[link]))
        self.assertEqual([x['url'] for x in primary_leads(d,[s],[])],[link['url']])
    def test_original_background_link_is_observed_not_automatically_approved(self):
        rows=background_links(DEF);self.assertEqual(rows[0]['url'],BACKGROUND);self.assertFalse(rows[0]['truth_verified'])
    def test_background_is_read_before_unrelated_platform_pages_without_schema_change(self):
        engine,qid,rid,d=self.prepare();tools=self.tools(d)
        sid=capture(engine,qid,rid,tools=tools,planner=lambda _:dict(action='finish'))
        visits=[u for k,u in tools.events if k=='page'];self.assertEqual(visits[0],BACKGROUND)
        self.assertNotIn(FAQ,visits);self.assertNotIn(MIRROR,visits);self.assertNotIn(WRONG,visits)
        snap=engine.repo.read_snapshot(qid,sid);material=next(m for m in snap['materials'] if m['source']['source_url']==BACKGROUND)
        self.assertEqual(material['source']['locator']['question_link_role'],'background');self.assertEqual(material['binding']['inclusion'],'pending')
    def test_repeated_query_reads_another_saved_candidate_after_handoff_cap_is_used(self):
        engine,qid,rid,d=self.prepare(background=False);tools=self.tools(d);calls=[];query=seed_plan(d,policy=QUERY_POLICY)['tavily']
        def planner(state):
            calls.append(state)
            return dict(action='search',query=query,evidence_gap='Need independent same-event historical records',anchor=dict(source_id='question',quote=TITLE)) if len(calls)<=3 else dict(action='finish')
        sid=capture(engine,qid,rid,tools=tools,planner=planner);visits=[u for k,u in tools.events if k=='page']
        self.assertEqual(len(visits),4);self.assertNotIn(MIRROR,visits);self.assertNotIn(FAQ,visits)
        self.assertEqual(len(engine.repo.get_question(qid)['searches']),2)
        self.assertIn('REPEATED_SEARCH_READ_SAVED_CANDIDATE',{g['code'] for g in engine.repo.read_snapshot(qid,sid)['gaps']})
    def test_original_page_limit_still_applies(self):
        engine,qid,rid,d=self.prepare(background=False);tools=self.tools(d)
        capture(engine,qid,rid,tools=tools,planner=lambda _:dict(action='search',query='Grand Slam of Darts historical nine-dart frequency',evidence_gap='Missing historic count',anchor=dict(source_id='question',quote=TITLE)),max_pages=2)
        self.assertLessEqual(len([u for k,u in tools.events if k=='page']),2)
    def test_sports_statistics_reporting_is_not_blocked_by_publisher_name(self):
        row=dict(url='https://news.bet365.com/en-gb/article/grand-slam-records-statistics/',title='Grand Slam of Darts records and nine-dart statistics')
        self.assertIsNone(candidate_exclusion(row));self.assertEqual(recovery_candidates(DEF,[row],[],policy=QUERY_POLICY),[row['url']])
    def test_darts_topic_never_matches_cpi_or_freedom_reports(self):
        self.assertFalse(topic_match(DEF,dict(url=WRONG,title='US Freedom in the World Report for 2050')))
        self.assertFalse(topic_match(DEF,dict(url='https://stats.gov/cpi',title='US core CPI annual inflation data')))
    def test_ordinary_subject_faq_remains_readable_and_question_rules_are_not_rewritten(self):
        self.assertIsNone(platform_exclusion('https://cfs.energy/faq'))
        self.assertIsNone(rejection(dict(source_url='https://cfs.energy/faq'),'SPARC technical FAQ with factual construction information.'))
        before=copy.deepcopy(DEF);background_links(DEF);self.assertEqual(DEF,before)
    def test_native_language_named_legal_document_remains_a_primary_lead(self):
        d=dict(title='Will Viktor Orbán be arrested in 2026?',resolution_criteria='Use credible legal reporting.',resolution_source_urls=['https://prosecutor.hu/'])
        link=dict(url='https://prosecutor.hu/orban-viktor-ugy/',title='Orbán Viktor ügyészségi közlemény')
        source=dict(source_id='s1',source_url='https://news.example.org/orban-viktor-case',status='extracted',locator=dict(outgoing_links=[link]))
        self.assertEqual([x['url'] for x in primary_leads(d,[source],[])],[link['url']])

if __name__=='__main__':unittest.main()
