"""Observed primary links and explicit missing-document searches, without outcome assumptions."""
from copy import deepcopy
import unittest
from pipeline_v2.source_trace import primary_leads,gap_search_check,anchor_catalog,bind_search_anchor
from pipeline_v2.acquisition import capture
from pipeline_v2.engine import collection_profile
import test_acquisition_recovery as recovery
from test_acquisition_recovery import TITLE,CRITERIA,REPORT,PRIMARY

D=dict(title=TITLE,resolution_criteria=CRITERIA,resolution_source_urls=['https://assembly.state.ny.us/leg/'])
SOURCE=dict(source_id='s1',status='extracted',source_url=REPORT,text_sha256='body',locator=dict(outgoing_links=[
    dict(url=PRIMARY,title='Official bill A11560 status'),
    dict(url='https://court.gov/privacy',title='Official statement'),
    dict(url='https://news.example/other',title='Official court statement')]))


class SourceTraceTests(unittest.TestCase):
    def test_only_observed_designated_or_gov_document_links_are_primary_leads(self):
        leads=primary_leads(D,[SOURCE],[])
        self.assertEqual([x['url'] for x in leads],[PRIMARY])
        self.assertEqual(leads[0]['parent_source_id'],'s1');self.assertFalse(leads[0]['truth_verified'])
        self.assertEqual(primary_leads(D,[SOURCE],[PRIMARY]),[])
    def test_literal_gap_anchor_and_site_must_be_observed(self):
        a=dict(query='Hochul A11560 prosecutor case',evidence_gap='Missing official case status',anchor=dict(source_id='s1',quote=CRITERIA))
        self.assertIsNone(gap_search_check(D,[SOURCE],{'body':CRITERIA},[],a))
        for change,expected in [('gap','SEARCH_GAP_REQUIRED'),('quote','SEARCH_ANCHOR_UNBOUND'),('query','SEARCH_ANCHOR_QUERY_MISMATCH'),('site','UNOBSERVED_SITE_FILTER')]:
            action=deepcopy(a)
            if change=='gap':action.pop('evidence_gap')
            elif change=='quote':action['anchor']['quote']='An invented factual assertion not in saved evidence.'
            elif change=='query':action['query']='Other unrelated subject'
            else:action['query']+=' site:invented-court.example'
            self.assertEqual(gap_search_check(D,[SOURCE],{'body':CRITERIA},[],action),expected)
        a['query']='site:assembly.state.ny.us Hochul A11560 status'
        self.assertIsNone(gap_search_check(D,[SOURCE],{'body':CRITERIA},[],a))
    def test_program_bound_ids_do_not_require_literal_transcription(self):
        anchors=anchor_catalog(D,[SOURCE],{'body':CRITERIA});selected=next(x for x in anchors if x['source_id']=='s1')
        action=dict(query='Hochul A11560 signed bill status',evidence_gap='Missing governor action',anchor_id=selected['anchor_id'])
        self.assertIsNone(gap_search_check(D,[SOURCE],{'body':CRITERIA},[],action))
        bound,error=bind_search_anchor(D,[SOURCE],{'body':CRITERIA},action)
        self.assertIsNone(error);self.assertEqual(bound['anchor']['quote'],CRITERIA)
        self.assertNotIn('anchor',action)
        self.assertEqual(gap_search_check(D,[SOURCE],{'body':CRITERIA+' revised'},[],action),'SEARCH_ANCHOR_UNBOUND')
        self.assertEqual(gap_search_check(D,[SOURCE],{'body':CRITERIA},[],dict(action,anchor_id='invented')),'SEARCH_ANCHOR_UNBOUND')
    def test_bad_transcription_gets_one_feedback_then_can_select_a_saved_id(self):
        case,rid,tools=self.prepare();calls=[]
        def planner(state):
            calls.append(state)
            if len(calls)==1:return dict(action='search',query='Hochul A11560 status',evidence_gap='Missing governor action',anchor=dict(source_id='s1',quote='Paraphrased quotation not saved anywhere.'))
            if len(calls)==2:
                selected=next(x for x in state['search_anchors'] if 'S10642' in x['quote'])
                return dict(action='search',query='Hochul S10642 governor action',evidence_gap='Missing governor action',anchor_id=selected['anchor_id'])
            return dict(action='finish',stop_reason='source_unavailable')
        sid=capture(case.engine,case.qid,rid,tools=tools,planner=planner)
        snap=case.engine.repo.read_snapshot(case.qid,sid);codes=[g['code'] for g in snap['gaps']]
        self.assertEqual(codes.count('SEARCH_ANCHOR_UNBOUND'),1)
        self.assertIn('BOUND_GAP_SEARCH',codes);self.assertEqual(len(case.engine.repo.get_question(case.qid)['searches']),3)
    def prepare(self,*,limit=2,primary_link=False):
        case=recovery.RecoveryTests();case.setUp();self.addCleanup(case.doCleanups)
        execution=collection_profile(offline=True);execution['parameters']['max_gap_searches']=limit
        rid=case.engine.new_run(case.qid,execution)
        class Tools(recovery.RecoveryTools):
            def result(self,provider,query,phase):
                result=super().result(provider,query,phase)
                result['results']=[dict(url=recovery.RULE,title=TITLE,snippet=CRITERIA,published_at=None,provider_score=None)]
                return result
            def page(self,url):
                observation,text=super().page(url)
                if primary_link and url==recovery.RULE:
                    observation['locator']=dict(outgoing_links=[dict(url=PRIMARY,title='Bill A11560 official status')])
                return observation,text
        return case,rid,Tools()
    def test_primary_trace_before_extra_search_has_parent_and_no_implicit_fact_approval(self):
        case,rid,tools=self.prepare(primary_link=True);actions=iter([
            dict(action='search',query='Hochul A11560 sponsor'),dict(action='finish',stop_reason='source_unavailable')])
        sid=capture(case.engine,case.qid,rid,tools=tools,planner=lambda _:next(actions))
        snap=case.engine.repo.read_snapshot(case.qid,sid)
        self.assertEqual(len(case.engine.repo.get_question(case.qid)['searches']),2)
        self.assertIn('PRIMARY_SOURCE_TRACE',{g['code'] for g in snap['gaps']})
        linked=next(m for m in snap['materials'] if m['source']['source_url']==PRIMARY)
        self.assertEqual(linked['source']['locator']['parent_source_id'],'s1')
        self.assertEqual(linked['binding']['inclusion'],'pending')
    def test_future_pending_stops_without_supplement_and_is_not_no(self):
        case,rid,tools=self.prepare();calls=[]
        def planner(state):calls.append(state['phase']);return dict(action='finish',stop_reason='future_pending')
        sid=capture(case.engine,case.qid,rid,tools=tools,planner=planner)
        self.assertEqual(calls,['acquisition']);snap=case.engine.repo.read_snapshot(case.qid,sid)
        self.assertIn('TARGET_DATA_PENDING',{g['code'] for g in snap['gaps']})
    def test_gap_limit_and_missing_anchor_stop_before_external_call(self):
        for limit,anchor,expected in [(0,True,'GAP_SEARCH_LIMIT'),(2,False,'SEARCH_ANCHOR_REQUIRED')]:
            case,rid,tools=self.prepare(limit=limit)
            action=dict(action='search',query='Hochul A11560 sponsor',evidence_gap='Missing signed bill status')
            if anchor:action['anchor']=dict(source_id='question',quote=CRITERIA)
            sid=capture(case.engine,case.qid,rid,tools=tools,planner=lambda _:action)
            self.assertIn(expected,{g['code'] for g in case.engine.repo.read_snapshot(case.qid,sid)['gaps']})
            self.assertEqual(len(case.engine.repo.get_question(case.qid)['searches']),2)
    def test_two_actions_without_new_leads_end_loop(self):
        case,rid,tools=self.prepare();queries=iter(['Hochul A11560 sponsor','Hochul A11560 governor calendar'])
        calls=[]
        def planner(state):
            calls.append(state['phase']);return dict(action='search',query=next(queries),evidence_gap='Missing signed bill status',anchor=dict(source_id='question',quote=CRITERIA))
        sid=capture(case.engine,case.qid,rid,tools=tools,planner=planner)
        self.assertEqual(len(calls),2)
        self.assertIn('NO_NEW_RETRIEVAL_LEADS',{g['code'] for g in case.engine.repo.read_snapshot(case.qid,sid)['gaps']})


if __name__=='__main__':unittest.main()
