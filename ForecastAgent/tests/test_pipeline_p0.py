"""Replay gates and recovery preserve clocks and never call research providers."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ForecastAgent import pipeline_readiness as replay, pipeline_health as health
from ForecastAgent.pipeline_recovery import save_blob,safe_path,recover
from ForecastAgent.market_store import store
from ForecastAgent.market_store.web_materials import hydrate
from ForecastAgent.competition_store import store as competition
from ForecastAgent.competition_store.ingest import repair_missing_local
from ForecastAgent.competition_store.ingest import project


AT='2026-10-15T02:00:00+00:00'
SEAL='2026-10-15T03:00:00+00:00'
KEY='polymarket:12'


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.local=self.root/'prediction_markets/store'
        self.db=store.connect(self.local/'market.sqlite3')
        self.market={'key':KEY,'venue':'polymarket','venue_id':'12','yes_token':'123','condition_id':'c',
                     'family':'polymarket:1','created_at':AT,'question':'Will the event occur?',
                     'rules':'Explicit event and date.','url':'https://example.org/event'}
        text='Published original observation with a zero value: 0.'
        url='https://example.org/report';body=hashlib.sha256(text.encode()).hexdigest()
        q={'market_research_key':KEY,'question':self.market['question'],'resolution_criteria':self.market['rules']}
        bundle={'request':q,'pages':{url:{'content':text,'content_sha256':body,'retrieved_at_utc':AT}}}
        state={'question':q,'sources':[{'source_id':'s','url':url,'body_sha256':body}],
               'evidence':[{'evidence_id':'e','source_id':'s','text':text,'start':0,'end':len(text)}]}
        self.result={'status':'completed','eventual_probability_yes':0.0,'price_means':{},
                     'state_sha256':store.digest(state),'response_sha256':'response','sealed_at':SEAL}
        self.task={'id':KEY,'market':self.market,'status':'completed','enrolled_at':AT,'analysis_stage':'analysis',
                   'forecast':{'sealed_at':SEAL,'arms':{'original':self.result}}}
        self.ingest(self.task)
        for path,value in [('analysis-input.json',bundle),('material-integrity.json',{'bundle_sha256':store.digest(bundle)}),
                           ('analysis/original/state.json',state),('analysis/original/result.json',self.result)]:
            raw=json.dumps(value,indent=2).encode();sha=hashlib.sha256(raw).hexdigest()
            local=self.local/'objects'/sha[:2]/sha;local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(raw)
            with self.db:store.append(self.db,'ref',KEY,{'path':'tasks/'+store.digest(KEY)+'/'+path,
                'sha256':sha,'size_bytes':len(raw),'source':{}},AT)
        hydrate(self.db,self.local)

    def ingest(self, task):
        with self.db:store.ingest_summary(self.db,{'generated_at':SEAL,
            'sources':{'s':{'state.json':{'tasks':[task]}}}},{'stream':'market-worker','run_id':'1'})

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_zero_score_exact_input_and_bodies_are_replayable(self):
        report=replay.market(self.db)
        self.assertEqual(report['ready'],1,report)
        replay.persist(self.db,report);replay.persist(self.db,replay.market(self.db))
        self.assertEqual(self.db.execute('SELECT count(*) FROM replay_manifests').fetchone()[0],1)

    def test_missing_body_does_not_erase_score_or_claim_readiness(self):
        with self.db:self.db.execute('DELETE FROM web_documents')
        report=replay.market(self.db)
        self.assertIn('local_body_missing_or_corrupt',report['tasks'][0]['gaps'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM forecasts').fetchone()[0],1)

    def test_changed_forecast_cannot_reuse_an_old_bound_packet(self):
        changed=json.loads(json.dumps(self.task))
        changed['forecast']['arms']['original']['eventual_probability_yes']=0.8
        self.ingest(changed)
        report=replay.market(self.db)
        self.assertEqual(report['ready'],0)
        self.assertIn('original_model_input_not_bound',report['tasks'][0]['gaps'])

    def test_missing_raw_input_is_not_hidden_by_display_projection(self):
        refs=self.db.execute("SELECT sha256 FROM material_refs WHERE path LIKE '%/original/state.json'").fetchall()
        for r in refs:(self.local/'objects'/r[0][:2]/r[0]).unlink()
        self.assertIn('native_replay_object_missing',replay.market(self.db)['tasks'][0]['gaps'])

    def test_future_capture_clock_fails_the_replay_gate(self):
        row=self.db.execute('SELECT id,payload FROM web_packets').fetchone();value=json.loads(row['payload'])
        value['materials']['sources'][0]['captured_at']='2026-10-16T00:00:00Z'
        with self.db:self.db.execute('UPDATE web_packets SET payload=? WHERE id=?',(json.dumps(value),row['id']))
        self.assertIn('capture_after_forecast',replay.market(self.db)['tasks'][0]['gaps'])

    def test_alerts_honor_retry_deadlines_and_resolve_without_deletion(self):
        task={**self.task,'status':'retry_wait','retry_at':'2026-10-16T00:00:00Z'};self.ingest(task)
        now=datetime(2026,10,15,4,tzinfo=timezone.utc)
        self.assertNotIn('admitted_task_overdue',{x['code'] for x in health.alerts(self.db,'market',now)})
        task.pop('retry_at');self.ingest(task)
        items=health.alerts(self.db,'market',now)
        self.assertIn('admitted_task_overdue',{x['code'] for x in items})
        health.persist(self.db,items);health.persist(self.db,items)
        count=self.db.execute('SELECT count(*) FROM pipeline_alerts').fetchone()[0]
        health.persist(self.db,[])
        self.assertEqual(count,self.db.execute('SELECT count(*) FROM pipeline_alerts').fetchone()[0])
        self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_alerts WHERE resolved_at IS NULL').fetchone()[0],0)

    def test_discovery_gaps_survive_a_successful_catalog(self):
        summary={'generated_at':SEAL,'sources':{'s':{'report.json':{'discovery_complete':False,
            'discovery_coverage':{'kalshi':{'coverage_lag_seconds':1900,'historical_gap_count':2}}}}}}
        with self.db:health.observations(self.db,summary,{'stream':'market-monitor','run_id':'2'})
        codes={x['code'] for x in health.alerts(self.db,'market')}
        self.assertIn('discovery_coverage_lag',codes);self.assertIn('historical_discovery_gap',codes)


class RecoveryTests(unittest.TestCase):
    def test_one_corrupt_competition_snapshot_does_not_hide_other_questions(self):
        with TemporaryDirectory() as folder:
            db=competition.connect(Path(folder)/'competition.sqlite3')
            for ident in ('101','102'):
                text='Original source';url='https://example.org/report';body=hashlib.sha256(text.encode()).hexdigest()
                bundle={'request':{'id':ident,'question':'Will the event occur?','resolution_criteria':'Explicit rule'},
                        'pages':{url:{'content':text,'content_sha256':body,'retrieved_at_utc':AT}}}
                state={'question':bundle['request'],'sources':[{'url':url,'body_sha256':body}]}
                request={'state':state,'model':'fixture'};result={'payload':{'question':int(ident),'probability_yes':0.3},'selection':'mercury_first_read'}
                prefix='tasks/'+ident+'/analysis-core-1.0.1/mercury-v1.0.1/'
                files={'tasks/'+ident+'/analysis-input.json':bundle,'tasks/'+ident+'/candidate.json':{
                    **result,'model_result_sha256':competition.native_hash(result)},prefix+'result.json':result,
                    prefix+'first-state.json':state,prefix+'first/request.json':request,
                    prefix+'first/identity.json':{'request_sha256':competition.native_hash(request)}}
                with db:
                    project(db,files,AT,'source')
                    raw=json.dumps(bundle).encode();digest=hashlib.sha256(raw).hexdigest()
                    competition.save(db,'native_objects',(digest,raw,len(raw),'plain'))
                    competition.save(db,'file_refs',(ident,'source','metaculus:'+ident,'tasks/'+ident+'/analysis-input.json',digest,len(raw),'verified_local_object','{}'))
            self.assertEqual(replay.competition(db)['ready'],2)
            with db:db.execute("UPDATE native_objects SET bytes=?,codec='gzip' WHERE sha256=(SELECT sha256 FROM file_refs WHERE id='101')",(b'corrupt',))
            report=replay.competition(db)
            self.assertEqual(report['ready'],1)
            self.assertIn('replay_validation_failed',report['tasks'][0]['gaps'])
            db.close()

    def remote_fixture(self, root, payloads):
        db=store.connect(root/'prediction_markets/store/market.sqlite3');db.close()
        db=competition.connect(root/'competitions/store/competition.sqlite3')
        path='tasks/101/analysis-input.json'
        with db:
            competition.identify(db,'101')
            for index,raw in enumerate(payloads):
                digest=hashlib.sha256(raw).hexdigest()
                competition.save(db,'file_refs',(str(index),'saved','metaculus:101',path,digest,len(raw),'missing','{}'))
        db.close()
        class Archive:
            members={path:True}
            def __init__(self,raw):self.raw=raw
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,name,maximum):return self.raw
        class Fake:
            repo='chenencc/futureeval-forecasting-agent'
            def __init__(self):self.client=self;self.calls=[]
            def api(self,url):
                self.calls.append(url)
                if '/workflows/' in url:return {'workflow_runs':[
                    {'id':i,'head_branch':'main','path':'.github/workflows/official_competition.yaml',
                     'head_repository':{'full_name':self.repo}} for i in range(len(payloads))]}
                i=int(url.split('/runs/')[1].split('/')[0])
                return {'artifacts':[{'id':i,'name':'futureeval-official-evidence-'+str(i),'expired':False}]}
            def open(self,artifact):return Archive(payloads[artifact['id']])
        return Fake()

    def test_multiple_old_hashes_of_one_path_recover_without_version_substitution(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);raws=[b'{"version":1}',b'{"version":2}']
            fake=self.remote_fixture(root,raws)
            report=recover(root,archives=fake)
            self.assertEqual(report['remaining_competition_objects'],[])
            self.assertEqual(len(report['recovered']),2)
            for raw in raws:
                digest=hashlib.sha256(raw).hexdigest()
                self.assertEqual((root/'blobs/sha256'/digest[:2]/digest).read_bytes(),raw)
            self.assertEqual(report['recaptures'],0)

    def test_recovery_bound_includes_unsuccessful_or_nonmatching_reads(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);raw=b'{"version":1}'
            fake=self.remote_fixture(root,[raw])
            report=recover(root,archives=fake,max_bytes=1)
            self.assertEqual(report['attempted_uncompressed_bytes'],0)
            self.assertEqual(len(report['remaining_competition_objects']),1)
            self.assertEqual(report['recovered'],[])

    def test_old_gaps_use_original_task_clocks_instead_of_latest_run_horizon(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);fake=self.remote_fixture(root,[b'{"old":true}'])
            db=competition.connect(root/'competitions/store/competition.sqlite3')
            with db:competition.task(db,'101',{'stage':'accepted','discovered_at_utc':AT,'accepted_at_utc':SEAL},AT,'saved')
            db.close()
            report=recover(root,archives=fake)
            self.assertEqual(report['remaining_competition_objects'],[])
            self.assertTrue(any('&created=' in call for call in fake.calls))

    def test_only_exact_hashes_and_safe_members_can_be_recovered(self):
        with TemporaryDirectory() as folder:
            raw=b'original';sha=hashlib.sha256(raw).hexdigest()
            target=save_blob(folder,raw,sha,len(raw))
            self.assertEqual(save_blob(folder,raw,sha,len(raw)),target)
            with self.assertRaises(ValueError):save_blob(folder,b'later',sha,len(raw))
            for path in ('../escape','C:/secret','/absolute','a\\b'):
                with self.assertRaises(ValueError):safe_path(path)

    def test_late_original_is_reprojected_after_an_already_imported_catalog(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);db=competition.connect(root/'competition.sqlite3')
            key='metaculus:101';source='official-worker:1'
            value={'request':{'id':'101','question':'Original question','resolution_criteria':'Original rule','as_of_utc':AT},'pages':{}}
            raw=json.dumps(value).encode();sha=hashlib.sha256(raw).hexdigest();path='tasks/101/analysis-input.json'
            with db:
                competition.identify(db,'101')
                competition.save(db,'imports',(source,'official-worker','1',AT,AT,'{}'))
                competition.save(db,'file_refs',('ref',source,key,path,sha,len(raw),'missing','{}'))
            fake=SimpleNamespace(root=root/'cache')
            self.assertEqual(repair_missing_local(db,fake,root)['repaired_references'],0)
            save_blob(root,raw,sha,len(raw))
            repaired=repair_missing_local(db,fake,root)
            self.assertEqual(repaired['remaining_missing'],0)
            self.assertEqual(db.execute('SELECT observed_at FROM bundles').fetchone()[0],AT)
            self.assertEqual(repair_missing_local(db,fake,root)['repaired_references'],0)
            self.assertEqual(db.execute('SELECT count(*) FROM native_recovery_receipts').fetchone()[0],1)
            db.close()

    def test_unconfirmed_or_failed_trigger_does_not_count_as_completion(self):
        with TemporaryDirectory() as folder:
            class Client:
                def api(self,path):
                    workflow=path.split('/workflows/')[1].split('/runs')[0]
                    return {'workflow_runs':[{'id':1,'path':'.github/workflows/'+workflow,'head_branch':'main',
                        'created_at':datetime.now(timezone.utc).isoformat(),'status':'completed','conclusion':'failure'}]}
            value=health.observe_triggers(folder,Client())
            self.assertEqual(sum(a['code']=='latest_workflow_failed' for a in value['alerts']),3)


if __name__=='__main__':unittest.main()
