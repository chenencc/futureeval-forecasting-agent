"""Both initial search channels are required independently of page/extraction success."""
from copy import deepcopy
import json,tempfile,unittest
from pathlib import Path
from pipeline_v2.acquisition import capture
from pipeline_v2.contracts import PipelineError,now
from pipeline_v2.engine import Engine,profile,find_run
from pipeline_v2.repository import Repository

class Tools:
    offline=True
    def __init__(self,failed=None,extract=False):self.events=[];self.failed=failed;self.extract=extract
    def result(self,provider,query,phase):
        self.events.append((provider,query))
        if self.failed==provider:
            exc=PipelineError('HTTP_429');exc.metadata=dict(requested_at=now(),captured_at=now());raise exc
        return dict(phase=phase,provider=provider,query=query,request_params={},effective_params=None,requested_at=now(),received_at=now(),status='ok',results=[dict(url='https://example.org/primary',title='Official target',snippet='Exact target and period',published_at=None,provider_score=None)],agent_visible_extras=None,provider_request_id='request-'+provider,adapter_version='fixture',error_code=None)
    def exa_search(self,query,phase='acquisition'):return self.result('exa',query,phase)
    def search(self,query,phase='acquisition'):return self.result('tavily',query,phase)
    def body(self,url):return dict(source_url=url,final_url=None,requested_at=now(),captured_at=now(),extracted_at=now(),status='extracted',evidence_kind='full_text',extraction_version='fixture-complete',http_status=200),'Issuer | Quarter | USD millions\nTarget | Q3 FY2027 | 2600'
    def page(self,url):
        self.events.append(('page',url))
        if self.extract:raise PipelineError('HTTP_403')
        return self.body(url)
    def page_fallback(self,url):self.events.append(('extract',url));return self.body(url)

class InitialSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.repo=Repository(self.tmp.name);self.engine=Engine(self.repo);self.qid='polymarket:90001'
        fixture=json.loads((Path(__file__).resolve().parent.parent/'fixtures/question_jsonl/binary.jsonl').read_text(encoding='utf-8'))
        self.definition=deepcopy(next(iter(fixture['question']['versions'].values()))['definition']);self.definition['resolution_source_urls']=['https://example.org/primary']
    def prepare(self):
        self.engine.question(self.qid,'https://polymarket.com/market/test',self.definition);return self.engine.new_run(self.qid,profile(offline=True))
    def check(self,tools,**kwargs):
        rid=self.prepare();sid=capture(self.engine,self.qid,rid,tools=tools,planner=lambda _:dict(action='finish'),**kwargs);r=self.repo.get_question(self.qid)
        self.assertEqual([s['provider'] for s in r['searches']],['exa','tavily']);self.assertEqual([e[0] for e in tools.events][:2],['exa','tavily'])
        self.assertEqual(find_run(r,rid)['snapshot_id'],sid);return r,self.repo.read_snapshot(self.qid,sid)
    def test_readable_official_rule_page_never_skips_either_seed_search(self):
        tools=Tools();r,snap=self.check(tools);self.assertEqual([e[0] for e in tools.events],['exa','tavily','page']);self.assertTrue(any(m['text'] for m in snap['materials']))
    def test_extraction_success_does_not_substitute_for_tavily_search(self):
        tools=Tools(extract=True);self.check(tools);self.assertEqual([e[0] for e in tools.events],['exa','tavily','page','extract'])
    def test_no_rule_url_does_not_duplicate_initial_tavily_search(self):
        self.definition['resolution_source_urls']=[];tools=Tools();self.check(tools);self.assertEqual([e[0] for e in tools.events].count('tavily'),1)
    def test_one_failed_search_does_not_prevent_other_channel_or_saved_body(self):
        for provider in ['exa','tavily']:
            with self.subTest(provider=provider):
                # Independent records prevent existing question-level Exa history from masking the call.
                self.qid='polymarket:'+('90002' if provider=='exa' else '90003')
                tools=Tools(failed=provider);rid=self.prepare();sid=capture(self.engine,self.qid,rid,tools=tools,planner=lambda _:dict(action='finish'))
                r=self.repo.get_question(self.qid);self.assertEqual([s['provider'] for s in r['searches']],['exa','tavily']);self.assertEqual(next(s for s in r['searches'] if s['provider']==provider)['status'],'failed')
                snap=self.repo.read_snapshot(self.qid,sid);self.assertTrue(any(m['text'] for m in snap['materials']));self.assertIn('HTTP_429',{g['code'] for g in snap['gaps']})
    def test_retry_reuses_saved_seed_attempts_without_minting_extra_searches(self):
        rid=self.prepare();aid,owner=self.engine.start(self.qid,rid,'acquisition');old=Tools()
        for provider in ['exa','tavily']:self.engine.search(self.qid,rid,aid,owner,old.result(provider,self.definition['title'],'acquisition'))
        self.engine.finish(self.qid,rid,'acquisition',aid,owner,'failed',code='INJECTED_AFTER_SEEDS')
        tools=Tools();capture(self.engine,self.qid,rid,tools=tools,planner=lambda _:dict(action='finish'))
        self.assertEqual([e[0] for e in tools.events],['page']);self.assertEqual(len(self.repo.get_question(self.qid)['searches']),2)
    def test_gap_driven_later_search_still_available(self):
        rid=self.prepare();tools=Tools();actions=iter([dict(action='finish'),dict(action='search',query=self.definition['title']+' official source',evidence_gap='Specific remaining evidence gap',anchor=dict(source_id='question',quote=self.definition['title'])),dict(action='finish')])
        capture(self.engine,self.qid,rid,tools=tools,planner=lambda _:next(actions))
        searches=self.repo.get_question(self.qid)['searches'];self.assertEqual([(s['provider'],s['phase']) for s in searches],[('exa','acquisition'),('tavily','acquisition'),('tavily','supplement')])

if __name__=='__main__':unittest.main()
