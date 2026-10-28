import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.research_query import ResearchQuery,POLICY,is_research,gap_count,validate
from pipeline_v2.acquisition import capture,LiveTools
from pipeline_v2.engine import Engine,collection_profile
from pipeline_v2.repository import Repository
from pipeline_v2.providers import Budget,Transport
from pipeline_v2.contracts import PipelineError,now
from test_pipeline_v2_initial_search import Tools
from test_raw_search import Response

QUERY=dict(query='Example Alpha historical counts baseline measurement drivers',research_goal='Historical same-subject counts and factual drivers.',
    why_this_query='The base searches seek current status; a historical baseline provides a different forecasting input.',evidence_kind='historical_baseline')

class ResearchQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        fixture=json.loads((Path(__file__).resolve().parents[1]/'fixtures/question_jsonl/binary.jsonl').read_text())
        self.definition=copy.deepcopy(next(iter(fixture['question']['versions'].values()))['definition'])
        self.definition.update(title='Will Example Alpha count exceed 9 in 2030?',resolution_criteria='Resolve Example Alpha count using the official report.',resolution_source_urls=['https://example.org/primary'])
        self.engine=Engine(Repository(self.root));self.qid='metaculus:999'
        self.engine.question(self.qid,'https://www.metaculus.com/questions/999/',self.definition)
        profile=collection_profile(offline=True);profile['parameters']['mandatory_research_query_policy']=POLICY
        self.rid=self.engine.new_run(self.qid,profile)
    def query(self,state):return copy.deepcopy(QUERY)
    def run_capture(self,tools=None,**kwargs):
        return capture(self.engine,self.qid,self.rid,tools=tools or Tools(),planner=lambda _:dict(action='finish'),research_planner=self.query,**kwargs)
    def session(self):return ResearchQuery(self.root,self.qid,self.rid,self.definition,[])
    def test_every_question_has_two_bases_then_one_generated_query_on_both_before_pages(self):
        tools=Tools();calls=[]
        def plan(state):calls.append(state);tools.events.append(('query-model',state['question']['title']));return self.query(state)
        sid=capture(self.engine,self.qid,self.rid,tools=tools,planner=lambda _:dict(action='finish'),research_planner=plan)
        self.assertEqual([k for k,_ in tools.events][:6],['exa','tavily','query-model','exa','tavily','page']);self.assertEqual(len(calls),1)
        searches=self.engine.repo.get_question(self.qid)['searches'];self.assertEqual(len(searches),4)
        self.assertEqual([s['query'] for s in searches[2:]],[QUERY['query']]*2);self.assertTrue(all(is_research(s) for s in searches[2:]))
        self.assertEqual(gap_count(searches),0);context=self.engine.repo.read_snapshot(self.qid,sid)['context']
        self.assertTrue(context['mandatory_research_query']['model_generated']);self.assertEqual(set(context['mandatory_research_query']['channels']),{'exa','tavily'})
    def test_retry_after_later_stage_interruption_never_regenerates_or_researches(self):
        tools=Tools();calls=[]
        def fail(_):raise RuntimeError('INTERRUPTED_AFTER_SEARCHES')
        with self.assertRaises(RuntimeError):capture(self.engine,self.qid,self.rid,tools=tools,planner=fail,research_planner=lambda s:(calls.append(s) or copy.deepcopy(QUERY)))
        second=Tools()
        capture(self.engine,self.qid,self.rid,tools=second,planner=lambda _:dict(action='finish'),research_planner=lambda _:self.fail('Repeated query model'))
        self.assertEqual(len(calls),1);self.assertFalse(any(k in {'exa','tavily'} for k,_ in second.events))
        self.assertEqual(len(self.engine.repo.get_question(self.qid)['searches']),4)
    def test_generated_search_does_not_spend_two_followup_gap_slots(self):
        profile=collection_profile(offline=True);profile['parameters']['mandatory_research_query_policy']=POLICY
        profile['parameters'].pop('retrieval_iteration_policy')
        self.rid=self.engine.new_run(self.qid,profile)
        class GrowingCandidates(Tools):
            def result(inner,provider,query,phase):
                result=super().result(provider,query,phase)
                if query not in {QUERY['query']} and len(inner.events)>4:
                    result['results']=[dict(url='https://example.org/series/'+str(len(inner.events)),title=self.definition['title'],snippet=self.definition['resolution_criteria'],published_at=None,provider_score=None)]
                return result
        tools=GrowingCandidates();actions=iter([
            dict(action='search',query='Example Alpha counts statistical series',evidence_gap='Missing historical count series',anchor=dict(source_id='question',quote=self.definition['title'])),
            dict(action='search',query='Example Alpha measurement definition methodology',evidence_gap='Missing measurement method',anchor=dict(source_id='question',quote=self.definition['title'])),
            dict(action='search',query='Example Alpha independent driver survey',evidence_gap='Missing independent driver',anchor=dict(source_id='question',quote=self.definition['title']))])
        sid=capture(self.engine,self.qid,self.rid,tools=tools,planner=lambda _:next(actions),research_planner=self.query)
        searches=self.engine.repo.get_question(self.qid)['searches'];self.assertEqual(len(searches),6);self.assertEqual(gap_count(searches),2)
        self.assertIn('GAP_SEARCH_LIMIT',{g['code'] for g in self.engine.repo.read_snapshot(self.qid,sid)['gaps']})
    def test_one_search_failure_does_not_skip_the_other(self):
        class Failing(Tools):
            def result(inner,provider,query,phase):
                if provider=='exa' and query==QUERY['query']:
                    exc=PipelineError('HTTP_502');exc.metadata=dict(requested_at=now(),captured_at=now());raise exc
                return super().result(provider,query,phase)
        sid=self.run_capture(Failing());summary=self.engine.repo.read_snapshot(self.qid,sid)['context']['mandatory_research_query']
        self.assertEqual(summary['channels']['exa']['status'],'failed');self.assertEqual(summary['channels']['tavily']['status'],'ok')
    def test_model_outage_still_runs_degraded_query_twice_and_records_downgrade(self):
        def unavailable(_):raise PipelineError('HTTP_503')
        sid=capture(self.engine,self.qid,self.rid,tools=Tools(),planner=lambda _:dict(action='finish'),research_planner=unavailable)
        context=self.engine.repo.read_snapshot(self.qid,sid)['context'];self.assertFalse(context['mandatory_research_query']['model_generated'])
        searches=self.engine.repo.get_question(self.qid)['searches'];self.assertEqual(len(searches),4);self.assertEqual(searches[2]['query'],searches[3]['query'])
        self.assertIn('RESEARCH_QUERY_DEGRADED',{g['code'] for g in self.engine.repo.read_snapshot(self.qid,sid)['gaps']})
    def test_invalid_query_types_and_community_search_are_not_sent(self):
        state=self.session().input
        for edit in [dict(query=['several queries']),dict(query='Example Alpha Polymarket odds'),dict(query='Another unrelated subject historical baseline'),dict(query='Example Alpha historical baseline site:invented.example')]:
            with self.subTest(edit=edit),self.assertRaises(PipelineError):validate({**QUERY,**edit},state)
    def test_reserved_query_is_not_retried_after_ambiguous_process_loss(self):
        session=self.session();session.state['status']='reserved';session.save();other=self.session()
        plan=other.plan(lambda _:self.fail('Unexpected model retry'))
        self.assertFalse(other.state['model_generated']);self.assertEqual(other.state['error_code'],'RESEARCH_QUERY_PRIOR_ATTEMPT_UNKNOWN');self.assertTrue(plan['query'])
    def test_saved_response_before_search_commit_is_reused_without_http(self):
        session=self.session();session.plan(self.query);obs=Tools().result('tavily',QUERY['query'],'acquisition')
        first=session.search('tavily',lambda _:obs);other=self.session()
        second=other.search('tavily',lambda _:self.fail('Repeated search'))
        self.assertEqual(first,second)
    def test_reserved_search_records_unknown_without_issuing_another_request(self):
        session=self.session();session.plan(self.query);session.state['channels']['exa']=dict(status='reserved',reserved_at=now());session.save()
        obs=self.session().search('exa',lambda _:self.fail('Unknown prior request was repeated'))
        self.assertEqual(obs['status'],'not_dispatched');self.assertTrue(obs['agent_visible_extras']['prior_attempt_may_have_dispatched'])
    def test_changed_question_cannot_reuse_saved_plan(self):
        session=self.session();session.plan(self.query);changed={**self.definition,'title':'Different identity'}
        with self.assertRaisesRegex(PipelineError,'FROZEN'):ResearchQuery(self.root,self.qid,self.rid,changed,[])
    def test_tampered_cached_query_is_not_reused(self):
        session=self.session();session.plan(self.query);session.state['plan']['query']='changed query';session.save()
        with self.assertRaisesRegex(PipelineError,'HASH_MISMATCH'):self.session()
    def test_tampered_cached_result_is_not_reused(self):
        session=self.session();session.plan(self.query);session.search('tavily',lambda _:Tools().result('tavily',QUERY['query'],'acquisition'))
        session.state['channels']['tavily']['observation']['results']=[];session.save()
        with self.assertRaisesRegex(PipelineError,'HASH_MISMATCH'):self.session().search('tavily',lambda _:self.fail('Unexpected network'))
    def test_a_different_provider_query_is_not_accepted(self):
        session=self.session();session.plan(self.query)
        with self.assertRaisesRegex(PipelineError,'QUERY_MISMATCH'):session.search('tavily',lambda _:Tools().result('tavily','Changed query','acquisition'))
    def test_exa_has_exactly_two_distinct_durable_slots(self):
        raw=json.dumps(dict(results=[dict(url='https://example.org/data',highlights=['A factual record'])])).encode();calls=[]
        def send(session,request,**kwargs):calls.append(request);return Response(raw)
        with patch.dict('os.environ',{'EXA_API':'fixture-only'}),patch('requests.Session.send',send):
            tools=LiveTools(Transport(Budget(self.root/'quota'),'fixture'),question_id=self.qid)
            tools.exa_search('Base query');tools.exa_research_search(QUERY['query'])
            with self.assertRaisesRegex(PipelineError,'ONCE_REQUEST'):tools.exa_research_search(QUERY['query'])
        self.assertEqual(len(calls),2)

if __name__=='__main__':unittest.main()
