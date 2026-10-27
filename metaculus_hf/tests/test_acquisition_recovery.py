"""Planner outage must not strand saved candidates or bypass retrieval budgets."""
from copy import deepcopy
import json,tempfile,unittest
from pathlib import Path
from pipeline_v2.acquisition import capture
from pipeline_v2.contracts import PipelineError,now
from pipeline_v2.engine import Engine,collection_profile
from pipeline_v2.repository import Repository
import test_pipeline_v2_initial_search as initial

RULE='https://www.nysenate.gov/legislation/bills/2025/S10642'
PRIMARY='https://assembly.state.ny.us/leg/?bn=A11560'
REPORT='https://news.example.org/hochul-data-center-bill'
TITLE='Will Hochul sign the data center moratorium bill in 2026?'
CRITERIA='Kathy Hochul signs the Responsible Data Center Development Act (A11560/S10642) by December 31, 2026.'


class RecoveryTools(initial.Tools):
    def __init__(self,fail_pages=False):super().__init__();self.fail_pages=fail_pages
    def result(self,provider,query,phase):
        result=super().result(provider,query,phase)
        result['results']=[dict(url=u,title=t,snippet=CRITERIA,published_at=None,provider_score=None) for u,t in [
            ('https://polymarket.com/event/hochul-a11560','Hochul A11560 S10642'),
            ('https://www.nysenate.gov/user/login','Hochul A11560 S10642 login'),
            (PRIMARY,'A11560 S10642 official bill status'),(REPORT,'Hochul data center moratorium bill reporting')]]
        return result
    def page(self,url):
        self.events.append(('page',url))
        if self.fail_pages and url!=RULE:raise PipelineError('HTTP_403')
        observation,text=self.body(url)
        return observation,'Synthetic fixture only: '+CRITERIA+' '+text
    def page_fallback(self,url):raise PipelineError('HTTP_403')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.engine=Engine(Repository(self.tmp.name));self.qid='polymarket:recovery'
        f=json.loads((Path(__file__).resolve().parents[1]/'fixtures/question_jsonl/binary.jsonl').read_text())
        definition=deepcopy(next(iter(f['question']['versions'].values()))['definition'])
        definition.update(title=TITLE,resolution_criteria=CRITERIA,resolution_source_urls=[RULE])
        self.engine.question(self.qid,'https://polymarket.com/event/recovery',definition)
        execution=collection_profile(offline=True)
        execution['parameters'].pop('source_trace_policy',None)  # Isolate the existing recovery policy in these fixtures.
        self.rid=self.engine.new_run(self.qid,execution)
    def run_failed(self,tools,**kwargs):
        def planner(_):raise PipelineError('HTTP_502')
        sid=capture(self.engine,self.qid,self.rid,tools=tools,planner=planner,**kwargs)
        return self.engine.repo.read_snapshot(self.qid,sid)
    def test_two_phase_outage_reads_only_two_saved_candidates_even_with_existing_body(self):
        tools=RecoveryTools();snap=self.run_failed(tools)
        self.assertEqual([u for k,u in tools.events if k=='page'],[RULE,PRIMARY,REPORT])
        self.assertEqual([k for k,_ in tools.events if k in {'exa','tavily'}],['exa','tavily'])
        self.assertEqual(snap['context']['planner_handoff_page_attempts'],2)
        self.assertEqual(len(snap['materials']),3)
        self.assertIn('PLANNER_CANDIDATE_HANDOFF',{g['code'] for g in snap['gaps']})
    def test_page_cap_still_bounds_automatic_handoff(self):
        tools=RecoveryTools();snap=self.run_failed(tools,max_pages=2)
        self.assertEqual([u for k,u in tools.events if k=='page'],[RULE,PRIMARY])
        self.assertEqual(snap['context']['planner_handoff_page_attempts'],1)
    def test_failed_pages_remain_gaps_and_do_not_trigger_unbounded_retrieval(self):
        tools=RecoveryTools(fail_pages=True);snap=self.run_failed(tools)
        self.assertEqual(len([u for k,u in tools.events if k=='page']),3)
        self.assertEqual(sum(bool(m['text']) for m in snap['materials']),1)
        self.assertTrue(all(m['binding']['inclusion']=='gap_only' for m in snap['materials'] if not m['text']))
    def test_successful_planner_does_not_trigger_outage_handoff(self):
        tools=RecoveryTools();sid=capture(self.engine,self.qid,self.rid,tools=tools,planner=lambda _:dict(action='finish'))
        self.assertEqual([u for k,u in tools.events if k=='page'],[RULE])
        self.assertEqual(self.engine.repo.read_snapshot(self.qid,sid)['context']['planner_handoff_page_attempts'],0)
    def test_reviewer_failure_does_not_add_a_third_outage_candidate(self):
        def fail(_):raise PipelineError('HTTP_502')
        tools=RecoveryTools();snap=self.run_failed(tools,reviewer=fail)
        self.assertEqual(len([u for k,u in tools.events if k=='page']),3)
        self.assertEqual(snap['context']['planner_handoff_page_attempts'],2)
    def test_default_planner_retries_502_once_with_same_payload_and_budget(self):
        import test_pipeline_v2_graph_retry as retry
        case=retry.GraphRetryTests();case.setUp();self.addCleanup(case.doCleanups)
        case.response(502);case.response()
        case.responses[-1][1]['choices'][0]['message']['content']='{"action":"finish"}'
        capture(self.engine,self.qid,self.rid,tools=RecoveryTools(),reviewer=lambda _:dict(materials=[]),max_rounds=1,supplement_rounds=0)
        self.assertEqual(len(case.requests),2)
        self.assertEqual(case.requests[0].body,case.requests[1].body)
        self.assertEqual(case.requests[0].headers['Authorization'],case.requests[1].headers['Authorization'])
        usage=self.engine.repo.get_question(self.qid)['runs'][0]['stages']['acquisition'][0]['usage']
        self.assertEqual(usage['model_requests'],2)
    def test_failed_primary_uses_ranked_saved_candidates_even_when_planner_finishes(self):
        class FailedPrimary(RecoveryTools):
            def page(self,url):
                if url==RULE:
                    self.events.append(('page',url));raise PipelineError('HTTP_403')
                return super().page(url)
        tools=FailedPrimary()
        sid=capture(self.engine,self.qid,self.rid,tools=tools,planner=lambda _:dict(action='finish'))
        self.assertEqual([u for k,u in tools.events if k=='page'],[RULE,PRIMARY,REPORT])
        snap=self.engine.repo.read_snapshot(self.qid,sid)
        self.assertEqual(snap['context']['planner_handoff_page_attempts'],2)
    def test_repeated_action_switches_to_saved_pages_despite_background_body(self):
        tools=RecoveryTools()
        sid=capture(self.engine,self.qid,self.rid,tools=tools,planner=lambda _:dict(action='search',query='same query'))
        self.assertEqual([u for k,u in tools.events if k=='page'],[RULE,PRIMARY,REPORT])
        snap=self.engine.repo.read_snapshot(self.qid,sid)
        self.assertEqual(snap['context']['planner_handoff_page_attempts'],2)
        self.assertEqual(sum(g['code']=='SEARCH_DEFERRED_FOR_SAVED_BODY' for g in snap['gaps']),2)


if __name__=='__main__':unittest.main()
