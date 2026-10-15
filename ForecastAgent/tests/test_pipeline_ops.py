"""Acceptance must distinguish pending work, historical gaps and lost scores."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ForecastAgent import pipeline_ops
from ForecastAgent.market_store import store
from ForecastAgent.market_store.tests.test_store import MARKET,KEY,AT
from ForecastAgent.competition_store import store as competition
from ForecastAgent.local_transfer import atomic_json


class PipelineAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.market=self.root/'prediction_markets/store'
        self.competition=self.root/'competitions/store'
        db=store.connect(self.market/'market.sqlite3');db.close()
        db=competition.connect(self.competition/'competition.sqlite3');db.close()
        for root in (self.market,self.competition):
            atomic_json(root/'latest-sync.json',{'status':'ok','finished_at':store.clock()})

    def tearDown(self):self.folder.cleanup()

    def test_admitted_pending_is_visible_but_not_falsely_failed(self):
        db=store.connect(self.market/'market.sqlite3')
        with db:store.append(db,'market',KEY,MARKET,AT)
        db.close()
        result=pipeline_ops.audit(self.root)
        self.assertEqual(result['status'],'ok_with_gaps')
        self.assertEqual(result['domains']['market']['awaiting_worker_checkpoint'],[KEY])

    def test_claimed_complete_without_score_is_a_failure(self):
        db=store.connect(self.market/'market.sqlite3')
        with db:store.ingest_summary(db,{'generated_at':AT,'sources':{'a':{'state.json':{
            'tasks':[{'id':KEY,'market':MARKET,'status':'completed'}]}}}}, {'stream':'market-worker'})
        db.close()
        result=pipeline_ops.audit(self.root)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['domains']['market']['completed_without_original_score'],[KEY])

    def test_accepted_receipt_must_match_saved_payload(self):
        db=competition.connect(self.competition/'competition.sqlite3')
        with db:
            competition.identify(db,'12')
            candidate={'payload':{'question':12,'probability_yes':0.2}}
            competition.candidate(db,'12',candidate,AT,'fixture')
            competition.receipt(db,'12',{'status':'accepted','confirmed_at_utc':AT,
                'payload':candidate['payload']},AT,'fixture')
        check=pipeline_ops.audit(self.root)
        self.assertEqual(check['domains']['competition']['errors'],[])
        self.assertEqual(check['domains']['competition']['replay']['not_ready'],1)
        with db:db.execute("UPDATE receipts SET payload='{}'")
        db.close()
        result=pipeline_ops.audit(self.root)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['domains']['competition']['errors'],['accepted_receipt_payload_mismatch'])

    def test_failed_or_stale_sync_cannot_look_healthy(self):
        atomic_json(self.market/'latest-sync.json',{'status':'failed','finished_at':'2026-01-01T00:00:00Z'})
        result=pipeline_ops.audit(self.root)
        self.assertEqual(result['status'],'failed')
        self.assertIn('last_domain_sync_not_successful',result['domains']['market']['errors'])

    def test_new_unbound_delivery_cannot_be_excused_as_historical(self):
        db=competition.connect(self.competition/'competition.sqlite3')
        with db:
            competition.task(db,'12',{'stage':'accepted'},AT,'fixture')
            competition.receipt(db,'12',{'status':'accepted','confirmed_at_utc':AT,
                'payload':{'question':12,'probability_yes':0.3}},AT,'fixture')
        db.close()
        # Reopening must not grow the frozen legacy exemption.
        competition.connect(self.competition/'competition.sqlite3').close()
        result=pipeline_ops.audit(self.root)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['domains']['competition']['new_accepted_without_bound_receipt'],['metaculus:12'])


if __name__=='__main__':unittest.main()
