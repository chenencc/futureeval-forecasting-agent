import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pipeline_v2.contracts import now,digest
from pipeline_v2.repository import Repository
from pipeline_v2.engine import Engine,collection_profile
from pipeline_v2.platforms import metaculus
from pipeline_v2.intelligence_package import load
from metaculus_agent.storage import RuntimeCheckpoint,dumps,sha,Conflict
from metaculus_agent.products import context,bundle,prefix,product,verify_context
from metaculus_agent.runner import Runner,initial_state,blind_post
from ForecastAgent.competition.live import live_request,rule_identity
from ForecastAgent.competition import mercury,platform
from ForecastAgent.analysis.distributions import payload

def post(kind='binary',ident=100):
    question=dict(id=ident,type=kind,title='Synthetic Example Alpha result in 2030',status='open',
        resolution_criteria='Resolve using the Example Alpha official report. https://example.org/official',
        fine_print='Use final observations only.',description='Synthetic fixture, not a real forecast.',
        scheduled_close_time='2030-10-01T00:00:00Z',scheduled_resolve_time='2030-10-02T00:00:00Z',
        my_forecasts=dict(latest=None,history=[]),community_prediction={'secret':'must_not_enter_models'})
    if kind=='multiple_choice':question['options']=['A','B','C']
    if kind in ('numeric','date'):
        lo,hi=(0,100) if kind=='numeric' else (1893456000,1924992000)
        question.update(scaling=dict(range_min=lo,range_max=hi,zero_point=None),inbound_outcome_count=200,
            open_lower_bound=False,open_upper_bound=False,unit='USD' if kind=='numeric' else 'UTC')
    return dict(id=ident+1000,title=question['title'],status='open',user_permission='forecaster',
        created_at='2026-10-20T00:00:00Z',question=question,comments=['not for models'])

class MemoryStore:
    def __init__(self):self.head='0';self.history={'0':{}};self.guards={};self.fail=False
    def exists(self,key):return key in self.history[self.head]
    def read(self,key,revision=None):return self.history[revision or self.head][key]
    def publish(self,updates):
        if self.fail:raise Conflict('INJECTED_CONFLICT')
        new=str(int(self.head)+1);self.history[new]={**self.history[self.head],**updates};self.head=new
        return dict(revision=new,verified=True,files={k:dict(sha256=sha(v),bytes=len(v)) for k,v in updates.items()})

def sealed(task,root):
    repo=Repository(root);engine=Engine(repo);item=metaculus(task['post'])[0];engine.question(**item)
    profile=collection_profile(offline=True);rid=engine.new_run(item['question_id'],profile)
    aid,owner=engine.start(item['question_id'],rid,'acquisition')
    text='Synthetic Example Alpha official report: the previous observation was 42 USD. This is a fixture only.'
    source=engine.source(item['question_id'],rid,aid,owner,dict(source_url='https://example.org/official',
        discovered_via='rule_url',status='extracted',evidence_kind='full_text',extraction_version='synthetic/1',
        requested_at=now(),captured_at=now(),extracted_at=now(),http_status=200),text)
    sid=engine.seal(item['question_id'],rid,aid,owner,[dict(source_id=source,inclusion='supporting',source_role='historical_baseline')],
        context={'fixture_only':True},temporal_policy='retrospective_unverified')
    record=repo.get_question(item['question_id'])
    package=dict(schema='intelligence-package/1',question_id=item['question_id'],definitions=record['question']['versions'],
        searches=record['searches'],evidence=record['evidence'])
    task.update(snapshot_id=sid,execution_profile=profile)
    return package

class Client:
    def __init__(self,p):self.value=p;self.posts=0;self.forecast=None;self.marker=None
    def account(self):return {'id':platform.BOT_ID}
    def post(self,_):return self.value
    def comments(self,_):
        return [] if self.marker is None else [dict(text=self.marker,author=dict(id=platform.BOT_ID),is_private=True,id=123)]
    def request(self,method,path,body):
        self.posts+=1;self.marker=body['comments'][0]['text'];candidate=body['forecasts'][0]
        p=candidate.get('probability_yes',.5)
        self.value['question']['my_forecasts']=dict(latest=dict(author_id=platform.BOT_ID,forecast_values=[1-p,p]),history=[])
        return dict(status=201)

class ProductTests(unittest.TestCase):
    def test_four_question_types_are_blind_and_roundtrip(self):
        for kind in ('binary','multiple_choice','numeric','date'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                p=post(kind);q=p['question'];clean=blind_post(p,q)
                request=live_request(clean,clean['question'])
                self.assertNotIn('community_prediction',json.dumps(request));self.assertNotIn('my_forecasts',json.dumps(clean))
                task=dict(id=str(q['id']),post_id=str(p['id']),run_id='abc123',started_at='2026-10-27T16:01:00Z',
                    rule_identity=rule_identity(p,q),post=clean,request=request)
                package=sealed(task,Path(folder)/'repo');value=context(task,package,task['snapshot_id'])
                raw=product('context',value);path=Path(folder)/'context.parquet';path.write_bytes(raw)
                self.assertEqual(load(path),value);self.assertIn('start_date=2026-10-28',prefix(task))
                adapted=bundle(value);packet=mercury.packet_for(adapted)
                self.assertEqual(packet['question'],request)
                self.assertEqual(adapted['pages']['https://example.org/official']['content'],verify_context(value)['materials'][0]['text'])
                if kind!='binary':self.assertEqual(mercury.distribution_spec(request)['kind'],kind)
                broken=copy.deepcopy(value);broken['intelligence']['evidence']['texts'][next(iter(package['evidence']['texts']))]='tampered'
                with self.assertRaises(Exception):verify_context(broken)
    def test_run_prefix_stays_on_original_start_date(self):
        self.assertEqual(prefix(dict(id='1',run_id='abc',started_at='2026-10-27T15:59:59Z')),'metaculus/start_date=2026-10-27/question_id=1/run_id=abc')

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=MemoryStore();self.state=initial_state('test');self.state['migration_complete']=True
        self.store.publish({'metaculus/_control/checkpoint.json':dumps(self.state)})
    def checkpoint(self,name):return RuntimeCheckpoint(self.store,self.root/name)
    def test_resume_files_and_budget_without_browser_auth(self):
        cp=self.checkpoint('a');state=cp.restore()
        for name in ['tasks/100/analysis/reservation.json','budgets/control/provider-budget.jsonl','tasks/100/browser/endpoint.json']:
            path=cp.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{"used":3}')
        cp.save(state);other=self.checkpoint('b');restored=other.restore()
        self.assertEqual(json.loads((other.root/'budgets/control/provider-budget.jsonl').read_text()),{'used':3})
        self.assertFalse((other.root/'tasks/100/browser/endpoint.json').exists())
    def test_unsafe_restore_blocked(self):
        cp=self.checkpoint('a');s={**self.state,'runtime':{'../../escape.json':'a'*64}}
        self.store.publish({cp.key:dumps(s)})
        with self.assertRaises(ValueError):cp.restore()
    def test_missing_checkpoint_is_not_empty_budget(self):
        with self.assertRaisesRegex(RuntimeError,'MIGRATION_CHECKPOINT_REQUIRED'):RuntimeCheckpoint(MemoryStore(),self.root/'new').restore()
    def test_collect_analyze_then_restart_submit_without_recollecting(self):
        p=post();client=Client(p);calls=[]
        def collector(task,p,root,checkpoint):calls.append('collect');return sealed(task,root/'capture')
        def analyzer(value,root,checkpoint):
            calls.append('analysis')
            return dict(snapshot_id=value['snapshot_id'],context_sha256=digest(value),candidate=dict(payload=payload(value['request'],{'probability_yes':.6}),comment='Saved original evidence',selection='fixture'))
        runner=Runner(self.checkpoint('a'),client,writer='test',collector=collector,analyzer=analyzer)
        result=runner.run([p]);self.assertEqual(result['failures'],0);self.assertEqual(calls,['collect','analysis']);self.assertEqual(client.posts,0)
        resumed=Runner(self.checkpoint('b'),client,writer='test',live=True,collector=lambda *_:self.fail('recollect'),analyzer=lambda *_:self.fail('reanalyze'))
        self.assertEqual(resumed.run([p])['failures'],0);self.assertEqual(client.posts,1)
        self.assertEqual(resumed.state['tasks']['100']['status'],'accepted');self.assertFalse((resumed.root/'tasks/100').exists())
        again=Runner(self.checkpoint('c'),client,writer='test',live=True)
        self.assertEqual(again.run([p])['processed'],0);self.assertEqual(client.posts,1)
    def test_conflict_aborts_before_external_calls(self):
        runner=Runner(self.checkpoint('a'),Client(post()),writer='test',collector=lambda *_:self.fail('provider must not run'))
        self.store.fail=True
        with self.assertRaises(Conflict):runner.run([post()])
    def test_server_handoff_required_before_live(self):
        self.state['migration_complete']=False;self.store.publish({'metaculus/_control/checkpoint.json':dumps(self.state)})
        with self.assertRaisesRegex(RuntimeError,'SERVER_HANDOFF_REQUIRED'):Runner(self.checkpoint('a'),Client(post()),writer='test',live=True)
    def test_existing_platform_forecast_skips_acquisition(self):
        p=post();p['question']['my_forecasts']['latest']=dict(author_id=platform.BOT_ID,forecast_values=[.5,.5])
        runner=Runner(self.checkpoint('a'),Client(p),writer='test',collector=lambda *_:self.fail('duplicate acquisition'))
        self.assertEqual(runner.run([p])['processed'],0)
    def test_ambiguous_post_is_never_reposted_after_restart_or_deadline(self):
        p=post();client=Client(p)
        def collector(task,p,root,checkpoint):return sealed(task,root/'capture')
        def analyzer(value,*_):return dict(snapshot_id=value['snapshot_id'],context_sha256=digest(value),candidate=dict(payload=payload(value['request'],{'probability_yes':.6}),comment='Saved evidence'))
        runner=Runner(self.checkpoint('a'),client,writer='test',collector=collector,analyzer=analyzer)
        self.assertEqual(runner.run([p])['failures'],0)
        # Simulate a connection failure after server acceptance, before local ACK.
        def ambiguous(method,path,body):
            client.posts+=1;raise ConnectionError('response was lost')
        client.request=ambiguous
        live=Runner(self.checkpoint('b'),client,writer='test',live=True)
        self.assertEqual(live.run([p])['failures'],1);self.assertEqual(client.posts,1)
        live.state['tasks']['100']['deadline_utc']='2020-01-01T00:00:00Z';live.checkpoint()
        resumed=Runner(self.checkpoint('c'),client,writer='test',live=True)
        self.assertEqual(resumed.run([p])['failures'],1);self.assertEqual(client.posts,1)
    def test_accepted_receipt_reconciles_even_after_closed_market(self):
        p=post();client=Client(p)
        def collector(task,p,root,checkpoint):return sealed(task,root/'capture')
        def analyzer(value,*_):return dict(snapshot_id=value['snapshot_id'],context_sha256=digest(value),candidate=dict(payload=payload(value['request'],{'probability_yes':.6}),comment='Saved evidence'))
        runner=Runner(self.checkpoint('a'),client,writer='test',collector=collector,analyzer=analyzer);runner.run([p])
        live=Runner(self.checkpoint('b'),client,writer='test',live=True)
        original=client.request
        def accepted_but_lost(method,path,body):original(method,path,body);raise ConnectionError('lost response')
        client.request=accepted_but_lost;live.run([p]);self.assertEqual(client.posts,1)
        p['question']['status']='closed'
        resumed=Runner(self.checkpoint('c'),client,writer='test',live=True)
        self.assertEqual(resumed.run([])['failures'],0);self.assertEqual(client.posts,1)
        self.assertEqual(resumed.state['tasks']['100']['status'],'accepted')

if __name__=='__main__':unittest.main()
