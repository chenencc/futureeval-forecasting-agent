"""Native import invariants and receipt/input isolation for the Web reader."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.competition_store import store
from ForecastAgent.competition_store.ingest import project,Native
from ForecastAgent.competition_store.read import open_readonly,read_task,read_body

AT='2026-10-15T03:00:00+00:00'

class CompetitionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=store.connect(self.root/'competition.sqlite3')
        self.body='Saved original source.\n[PDF page 2]\nMetric\tValue\nZero\t0'
        self.sha=hashlib.sha256(self.body.encode()).hexdigest()
        self.url='https://example.org/report'
        self.bundle={'request':{'id':'101','question_type':'multiple_choice','question':'Which option?',
            'resolution_criteria':'Choose exactly the resolving option.','options':['A','B']},
            'pages':{self.url:{'content':self.body,'content_sha256':self.sha,'captured_at_utc':AT}}}
        self.candidate={'payload':{'question':101,'probability_yes_per_category':{'A':0.0,'B':1.0}},'selection':'mercury_first_read'}

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def files(self):
        return {'campaign.json':{'tasks':{'101':{'post_id':'900','stage':'accepted'}}},
            'tasks/101/analysis-input.json':self.bundle,'tasks/101/candidate.json':self.candidate}

    def test_nonbinary_zero_metadata_and_idempotence(self):
        files=self.files()
        with self.db: project(self.db,files,AT,'run:1');project(self.db,files,AT,'run:1')
        self.assertEqual(self.db.execute('SELECT count(*) FROM forecasts').fetchone()[0],1)
        detail=read_task(self.db,'metaculus:101')
        self.assertEqual(detail['question']['options'],['A','B'])
        self.assertEqual(detail['forecasts'][0]['payload'],self.candidate['payload'])
        self.assertEqual(detail['overview']['kind'],'multiple_choice')
        self.assertEqual(detail['availability']['bound_model_inputs'],0)

    def test_confirmed_receipt_matches_its_own_earlier_forecast(self):
        with self.db:
            project(self.db,self.files(),AT,'run:1')
            receipt={'payload':self.candidate['payload'],'status':'accepted','confirmed_at_utc':AT}
            store.receipt(self.db,'101',receipt,AT,'run:1')
            store.candidate(self.db,'101',{'payload':{'question':101,'probability_yes_per_category':{'A':0.7,'B':0.3}}},'2026-10-15T04:00:00Z','run:2')
        value=read_task(self.db,'metaculus:101')
        self.assertTrue(value['receipts'][0]['verified_binding'])
        self.assertNotEqual(value['receipts'][0]['forecast_id'],value['forecasts'][-1]['id'])

    def test_unconfirmed_and_unmatched_are_not_accepted(self):
        with self.db:
            project(self.db,self.files(),AT,'run:1')
            store.receipt(self.db,'101',{'payload':self.candidate['payload'],'status':'accepted'},AT,'run:1')
            store.receipt(self.db,'101',{'payload':{'question':101,'continuous_cdf':[0,0.5,1]},'status':'accepted','confirmed_at_utc':AT},AT,'run:2')
        self.assertEqual({r['status'] for r in self.db.execute('SELECT status FROM receipts')},{'unconfirmed','accepted_unbound'})
        with self.assertRaises(ValueError):store.receipt(self.db,'102',{'payload':self.candidate['payload']},AT,'x')

    def test_old_snapshots_cannot_replace_new_metadata(self):
        with self.db:
            store.question(self.db,{'id':101,'status':'resolved','resolution':'A','options':['A','B']},'fall-futureeval-2026','2026-10-16T00:00:00Z','new')
            store.question(self.db,{'id':101,'status':'open'},'fall-futureeval-2026',AT,'old')
        self.assertEqual(self.db.execute('SELECT status FROM latest_questions').fetchone()[0],'resolved')

    def test_readonly_and_original_display_body_hash(self):
        with self.db:project(self.db,self.files(),AT,'run:1')
        reader=open_readonly(self.root)
        with self.assertRaises(Exception):reader.execute('DELETE FROM questions')
        sha=reader.execute('SELECT display_sha256 FROM documents').fetchone()[0]
        self.assertEqual(read_body(reader,sha),self.body.encode());reader.close()
        with self.db:self.db.execute("UPDATE documents SET text='bad'")
        with self.assertRaises(ValueError):read_body(self.db,sha)

    def test_actual_model_input_chain_and_corruption(self):
        state={'question':self.bundle['request'],'sources':[{'source_id':'s1','url':self.url,'body_sha256':self.sha}],
            'evidence':[{'source_id':'s1','text':self.body[:5],'start':0,'end':5}]}
        request={'state':state,'model':'saved-model'}
        result={'payload':self.candidate['payload'],'selection':'mercury_first_read'}
        files=self.files();files['tasks/101/candidate.json']={**self.candidate,'model_result_sha256':store.native_hash(result)}
        prefix='tasks/101/analysis-core-1.0.1/mercury-v1.0.1/'
        files.update({prefix+'result.json':result,prefix+'first-state.json':state,prefix+'first/request.json':request,
            prefix+'first/identity.json':{'request_sha256':store.native_hash(request)}})
        with self.db:project(self.db,files,AT,'run:1')
        detail=read_task(self.db,'metaculus:101');self.assertEqual(detail['decision_inputs'][0]['status'],'model_input_verified')
        self.assertEqual(detail['decision_inputs'][0]['evidence'][0]['text'],self.body[:5])
        files[prefix+'first/identity.json']={'request_sha256':'wrong'}
        with self.db:project(self.db,files,AT,'run:2')
        self.assertIn('integrity_failed',[i['status'] for i in read_task(self.db,'metaculus:101')['decision_inputs']])

    def test_cross_question_and_corrupt_material_refused(self):
        with self.assertRaises(ValueError):store.bundle(self.db,'102',self.bundle,'analysis-input',AT,'x')
        corrupt=json.loads(json.dumps(self.bundle));corrupt['pages'][self.url]['content']='changed'
        with self.assertRaises(ValueError):store.bundle(self.db,'101',corrupt,'analysis-input',AT,'x')

    def test_native_original_hash_and_safe_path_before_transport(self):
        class Stub:
            root=Path(self.tmp.name)
        reader=Native(self.db,Stub(),{}, {}, self.root)
        with self.assertRaises(ValueError):reader.read({'path':'../private.json','sha256':'a'*64,'size_bytes':1})
        with self.assertRaises(FileNotFoundError):reader.read({'path':'tasks/101/x.json','sha256':'a'*64,'size_bytes':1},local_only=True)

    def test_local_nonbinary_metadata_survives_inventory_only_archives(self):
        from ForecastAgent.competition_store.local import market_pulse
        root=self.root/'tournaments/market-pulse-26q4'
        data={
            'deliveries/v1/tasks/101/submission.json':{'payload':self.candidate['payload'],'status':'accepted','confirmed_at_utc':AT},
            'analysis/v1/tasks/101/result.json':self.candidate,
            'snapshots/v1/inventory.json':{'retrieved_at_utc':AT,'rows':[{'question_id':101,'post_id':900,'type':'multiple_choice','title':'Which option?'}]},
            'snapshots/v1/inputs/101.json':{'request':{**self.bundle['request'],'scaling':{'range_min':0,'range_max':10},'inbound_outcome_count':200}}}
        for name,value in data.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')
        market_pulse(self.db,self.root)
        detail=read_task(self.db,'metaculus:101')
        self.assertEqual(detail['question']['scaling']['range_min'],0)
        self.assertEqual(detail['question']['options'],['A','B'])
        self.assertEqual(detail['question']['post_id'],900)
        self.assertEqual(detail['question']['status'],'not_refreshed')
        self.assertTrue(detail['receipts'][0]['verified_binding'])
        self.assertEqual(self.db.execute("SELECT question_key FROM file_refs WHERE path LIKE '%101.json'").fetchone()[0],'metaculus:101')
        with self.db:self.db.execute("UPDATE file_refs SET question_key=NULL WHERE path LIKE '%101.json'")
        market_pulse(self.db,self.root)
        self.assertEqual(self.db.execute("SELECT question_key FROM file_refs WHERE path LIKE '%101.json'").fetchone()[0],'metaculus:101')

if __name__=='__main__':unittest.main()
