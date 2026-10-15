"""Storage, identity, temporal and failure-contract regressions."""
import json
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.market_store.store import append,connect,ingest_summary,records_since,stats,digest
from ForecastAgent.market_store.collector import collect
from ForecastAgent.market_store.providers import normalize_poly,normalize_kalshi,Retryable,history,venue_status

AT='2026-10-15T02:00:00+00:00'
KEY='polymarket:12'
MARKET={'key':KEY,'venue':'polymarket','venue_id':'12','yes_token':'123456','condition_id':'c',
        'family':'polymarket:3','created_at':'2026-10-14T02:00:00+00:00','question':'Example',
        'rules':'Exact rule','rules_sha256':'r','url':'https://polymarket.com/event/example'}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=connect(Path(self.tmp.name)/'db.sqlite3')
        with self.db: append(self.db,'market',KEY,MARKET,AT)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def summary(self,status='completed',at=AT):
        return {'generated_at':at,'sources':{'a':{'state.json':{'tasks':[{'id':KEY,'market':MARKET,
            'status':status,'stage':'done','capture_summary':{'readable':3,'gaps':['pdf_missing']},
            'forecast':{'sealed_at':AT,'arms':{'original':{'status':'completed','eventual_probability_yes':0.4}}}}]}}}}

    def test_idempotent_import_and_all_stage_data(self):
        with self.db:
            ingest_summary(self.db,self.summary(),{'run_id':'1'})
            ingest_summary(self.db,self.summary(),{'run_id':'1'})
            ingest_summary(self.db,self.summary(at='2026-10-15T03:00:00Z'),{'run_id':'2'})
        self.assertEqual(stats(self.db)['task_versions'],1)
        self.assertEqual(stats(self.db)['forecasts'],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM task_observations').fetchone()[0],2)
        payload=json.loads(self.db.execute('SELECT data FROM latest_tasks').fetchone()[0])
        self.assertEqual(payload['capture_summary']['gaps'],['pdf_missing'])

    def test_frozen_identity_rejects_rotation(self):
        with self.assertRaisesRegex(ValueError,'identity changed'):
            with self.db: append(self.db,'market',KEY,{**MARKET,'yes_token':'999'},AT)
        self.assertEqual(stats(self.db)['markets'],1)

    def test_content_reobservation_keeps_original_and_independent_receipt(self):
        value={'status':'completed','id':KEY}
        ident=digest({'kind':'task_payload','key':KEY,'sha256':digest(value)})
        with self.db:
            append(self.db,'task_payload',KEY,value,AT,ident=ident)
            self.assertFalse(append(self.db,'task_payload',KEY,value,'2026-10-15T07:25:52Z',ident=ident))
            self.assertFalse(append(self.db,'task_payload',KEY,value,'2026-10-15T07:25:52Z',ident=ident))
        self.assertEqual(self.db.execute('SELECT observed_at FROM records WHERE id=?',(ident,)).fetchone()[0],AT)
        self.assertEqual(self.db.execute('SELECT count(*) FROM replication_receipts').fetchone()[0],1)
        with self.assertRaisesRegex(ValueError,'collision'):
            append(self.db,'task_payload',KEY,dict(value,status='different'),'2026-10-15T07:25:52Z',ident=ident)

    def test_same_content_reference_retains_both_publication_locations(self):
        value={'path':'tasks/a/body.json','sha256':'a'*64,'size_bytes':100,'source':{'run_id':'first'}}
        ident=digest({'kind':'ref','key':KEY,'path':value['path'],'sha256':value['sha256']})
        later=dict(value,source={'run_id':'later'})
        with self.db:
            append(self.db,'ref',KEY,value,AT,ident=ident)
            self.assertTrue(append(self.db,'ref',KEY,later,'2026-10-15T07:25:52Z',ident=ident))
            self.assertFalse(append(self.db,'ref',KEY,later,'2026-10-15T07:25:52Z',ident=ident))
        self.assertEqual(self.db.execute('SELECT count(*) FROM material_refs').fetchone()[0],2)
        self.assertEqual(json.loads(self.db.execute('SELECT data FROM records WHERE id=?',(ident,)).fetchone()[0]),value)
        with self.assertRaisesRegex(ValueError,'collision'):
            append(self.db,'ref',KEY,dict(later,size_bytes=101),AT,ident=ident)

    def test_arbitrary_replication_ids_still_reject_changed_observation_clocks(self):
        with self.db:
            append(self.db,'task_payload',KEY,{'id':KEY},AT,ident='arbitrary')
        with self.assertRaisesRegex(ValueError,'collision'):
            append(self.db,'task_payload',KEY,{'id':KEY},'2026-10-15T03:00:00Z',ident='arbitrary')

    def test_stale_pipeline_import_does_not_overwrite(self):
        with self.db:
            ingest_summary(self.db,self.summary(),{'run_id':'new'})
            ingest_summary(self.db,self.summary('running','2026-10-14T02:00:00Z'),{'run_id':'old'})
        self.assertEqual(self.db.execute('SELECT pipeline_status FROM market_overview').fetchone()[0],'completed')

    def test_point_revisions_preserved_and_replication_idempotent(self):
        point={'kind':'provider_history_price','point_at':100,'resolution_seconds':60,'price':0.3,
               'response_sha256':'sha','run_id':'1'}
        with self.db:
            append(self.db,'point',KEY,point,'2026-10-15T02:00:00Z')
            append(self.db,'point',KEY,{**point,'price':0.4},'2026-10-15T03:00:00Z')
        self.assertEqual(self.db.execute('SELECT price FROM latest_prices').fetchone()[0],0.4)
        dest=connect(Path(self.tmp.name)/'copy.sqlite3')
        with dest:
            for _ in range(2):
                for r in records_since(self.db,0): append(dest,r['kind'],r['key'],r['data'],r['observed_at'],ident=r['id'])
        self.assertEqual(stats(dest)['price_points'],2)
        dest.close()

    def test_late_old_cursor_does_not_rewind(self):
        with self.db:
            append(self.db,'cursor',KEY,{'scanned_until':20,'status':'caught_up','observed_at':AT},AT)
            append(self.db,'cursor',KEY,{'scanned_until':10,'status':'backfill','observed_at':'2026-10-14T02:00:00Z'},'2026-10-14T02:00:00Z')
        self.assertEqual(self.db.execute('SELECT scanned_until FROM cursors').fetchone()[0],20)


class FakeHTTP:
    def __init__(self,fail=False,loop=False): self.requests=0;self.fail=fail;self.loop=loop
    def get(self,url,params=None):
        self.requests+=1
        receipt={'sha256':'s','received_at_epoch':1792029600}
        if 'gamma-api' in url:
            return {'id':'12','outcomes':['Yes','No'],'clobTokenIds':['123456','2'],'conditionId':'c'},receipt
        if self.fail: raise Retryable(503)
        return {'data':[{'timestamp':params['start']+1,'price':'0.4','resolution_seconds':60}],
                'has_more':self.loop,'next_cursor':'repeat' if self.loop else None},receipt


class CollectorTests(unittest.TestCase):
    setUp=StoreTests.setUp
    tearDown=StoreTests.tearDown

    def test_failed_and_truncated_windows_never_advance(self):
        for fake in (FakeHTTP(fail=True),FakeHTTP(loop=True)):
            result=collect(self.db,self.tmp.name,'1',now=1792029600,http=fake)
            self.assertTrue(result['errors'])
            row=self.db.execute('SELECT scanned_until,status FROM cursors').fetchone()
            self.assertIsNone(row['scanned_until'])
            self.assertEqual(row['status'],'retry_wait')
            with self.db: self.db.execute('DELETE FROM cursors')

    def test_success_and_overlap_do_not_duplicate_prices(self):
        first=collect(self.db,self.tmp.name,'1',now=1792029600,http=FakeHTTP())
        self.assertFalse(first['errors'])
        self.assertEqual(stats(self.db)['price_points'],1)
        self.assertEqual(first['model_requests'],0)

    def test_recent_tail_is_separate_from_old_backfill(self):
        with self.db:
            self.db.execute("UPDATE markets SET created_at='2026-10-10T02:00:00Z'")
        result=collect(self.db,self.tmp.name,'1',now=1792029600,http=FakeHTTP())
        self.assertFalse(result['errors'])
        cursor=json.loads(self.db.execute('SELECT data FROM cursors').fetchone()[0])
        self.assertEqual(cursor['live_scanned_until'],1792029600)
        self.assertLess(cursor['scanned_until'],cursor['live_scanned_until'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM history_windows').fetchone()[0],2)

    def test_monitor_does_not_overwrite_worker_status(self):
        a=StoreTests.summary(self)
        b=StoreTests.summary(self,'enrolled','2026-10-15T03:00:00Z')
        with self.db:
            ingest_summary(self.db,a,{'stream':'market-worker'})
            ingest_summary(self.db,b,{'stream':'market-monitor'})
        self.assertEqual(self.db.execute('SELECT pipeline_status FROM market_overview').fetchone()[0],'completed')
        self.assertEqual(self.db.execute('SELECT status FROM latest_discovery').fetchone()[0],'enrolled')

    def test_hot_checkpoint_leaves_full_delta_replayable(self):
        from ForecastAgent.market_store.cli import compact_checkpoint
        with self.db:
            point={'kind':'provider_history_price','point_at':100,'resolution_seconds':60,
                   'price':0.3,'response_sha256':'s','run_id':'1'}
            append(self.db,'point',KEY,point,AT)
            append(self.db,'cursor',KEY,{'scanned_until':10000,'live_scanned_until':20000,'status':'backfill','observed_at':AT},AT)
        records=list(records_since(self.db,0))
        compact_checkpoint(self.db)
        self.assertEqual(stats(self.db)['price_points'],0)
        target=connect(Path(self.tmp.name)/'archive.sqlite3')
        with target:
            for r in records: append(target,r['kind'],r['key'],r['data'],r['observed_at'],ident=r['id'])
        self.assertEqual(stats(target)['price_points'],1)
        target.close()


class NormalizeTests(unittest.TestCase):
    def test_nested_provider_pagination_and_unresolved_terminal_tick(self):
        class Provider(FakeHTTP):
            def get(self,url,params=None):
                if 'gamma-api' in url: return super().get(url,params)
                self.requests+=1
                return {'data':[{'timestamp':20,'price':'0.0505','resolution_seconds':0}],
                    'pagination':{'has_more':False,'next_cursor':None}}, {'sha256':'h','received_at_epoch':100}
        provider=Provider()
        status,_=venue_status(provider,MARKET)
        points,_,_=history(provider,MARKET,0,20,20)
        self.assertEqual(status['status'],'open')
        self.assertIsNone(status['result'])
        self.assertEqual(points[0][0]['kind'],'provider_unbucketed_terminal_point')

    def test_poly_milliseconds_settlement_and_sparse_points(self):
        raw={'data':[{'timestamp':1200000,'price':0.5,'resolution_seconds':60}]}
        self.assertEqual(normalize_poly(raw,1000000,1300000)[0]['point_at'],1200000)
        payout=normalize_poly({'data':[{'timestamp':200,'price':1,'resolution_seconds':0}]},100,200)[0]
        self.assertEqual(payout['kind'],'provider_unbucketed_terminal_point')
        self.assertEqual(len(normalize_poly({'data':[]},0,100)),0)

    def test_bad_prices_schema_and_time_fail(self):
        for value in ('NaN',1.2,True):
            with self.assertRaises(ValueError):
                normalize_poly({'data':[{'timestamp':10,'price':value,'resolution_seconds':60}]},0,20)
        with self.assertRaises(ValueError): normalize_poly({'data':[{'timestamp':100,'price':0.4,'resolution_seconds':60}]},0,20)

    def test_kalshi_fixed_dollars_and_exact_identity(self):
        raw={'ticker':'T','candlesticks':[{'end_period_ts':100,'yes_bid':{'close_dollars':'0.35'},
            'yes_ask':{'close_dollars':'0.45'},'price':{'close_dollars':'0.41'}}]}
        rows=normalize_kalshi(raw,'T',0,100)
        self.assertAlmostEqual(rows[0]['price'],0.4)
        self.assertEqual(rows[1]['price'],0.41)
        with self.assertRaises(ValueError): normalize_kalshi(raw,'OTHER',0,100)
        raw['candlesticks'][0]['yes_bid']['close_dollars']='0.9'
        with self.assertRaises(ValueError): normalize_kalshi(raw,'T',0,100)


if __name__=='__main__': unittest.main()
