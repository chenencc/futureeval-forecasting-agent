"""An actual cloud publication can be restored and replayed locally without HTTP."""
import argparse
from contextlib import redirect_stdout
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from ForecastAgent.market_store import cli
from ForecastAgent.market_store.collector import collect
from ForecastAgent.market_store.github_io import bounded_gunzip,REPO
from ForecastAgent.market_store.providers import venue_status,Retryable,history
from ForecastAgent.market_store.store import connect,ingest_summary,append,stats
from test_store import AT,KEY,MARKET,FakeHTTP


class PublicationTests(unittest.TestCase):
    def test_cloud_checkpoint_delta_restore_and_local_replay(self):
        class EmptyArchives:
            repo=REPO
            def __init__(self,*args,**kwargs): pass
            def latest(self,*args): return None
        def catalog(db,archives,stream):
            summary={'generated_at':AT,'sources':{'s':{'state.json':{'tasks':[
                {'id':KEY,'market':MARKET,'status':'completed'}]}}}}
            with db: ingest_summary(db,summary,{'stream':stream,'run_id':'1'})
            return {'stream':stream}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'cloud'
            args=argparse.Namespace(root=str(root),branch='main',max_requests=20,max_markets=3,seconds=30)
            def collection(db,objects,run_id,**kwargs):
                return collect(db,objects,run_id,now=1792029600,http=FakeHTTP())
            with patch.object(cli,'Archives',EmptyArchives),patch.object(cli,'import_catalog',catalog),\
                 patch.object(cli,'collect',collection),patch.dict('os.environ',{'GITHUB_RUN_ID':'123'}),redirect_stdout(io.StringIO()):
                cli.cloud(args)
            output=root/'publication'
            manifest=json.loads((output/'manifest.json').read_bytes())
            raw=bounded_gunzip((output/'market.sqlite3.gz').read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(),manifest['database_sha256'])
            self.assertEqual(manifest['checkpoint_scope'],'control_and_overlap_cache')
            target=connect(Path(folder)/'local.sqlite3')
            records=[json.loads(line) for line in gzip.decompress((output/'delta.jsonl.gz').read_bytes()).splitlines()]
            with target:
                for _ in range(2):
                    for record in records:
                        append(target,record['kind'],record['key'],record['data'],record['observed_at'],ident=record['id'])
            self.assertEqual(stats(target)['markets'],1)
            self.assertEqual(stats(target)['price_points'],1)
            self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            target.close()
            artifact={'id':55,'workflow_run':{'id':123}}
            class Artifact:
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def read(self,name,maximum):
                    value=(output/name).read_bytes()
                    if len(value)>maximum: raise ValueError('Read bound')
                    return value
            class FakeArchives:
                repo=REPO
                def __init__(self,*args,**kwargs): pass
                def latest(self,*args): return artifact
                def open(self,*args): return Artifact()
                def pending_history(self,db):
                    return ([] if db.execute("SELECT 1 FROM imports WHERE source_id='history-run:123'").fetchone() else [artifact]),[]
            local_root=Path(folder)/'local-store'
            with patch.object(cli,'Archives',FakeArchives),patch.object(cli,'import_catalog',catalog),redirect_stdout(io.StringIO()):
                cli.mirror(argparse.Namespace(root=str(local_root)))
                cli.mirror(argparse.Namespace(root=str(local_root)))
            local=connect(local_root/'market.sqlite3')
            self.assertEqual(stats(local)['price_points'],1)
            self.assertEqual(local.execute('SELECT count(*) FROM imports').fetchone()[0],1)
            local.close()

    def test_compressed_expansion_is_bounded(self):
        with self.assertRaises(ValueError): bounded_gunzip(gzip.compress(b'x'*1000),maximum=10)

    def test_explicit_winner_not_display_price(self):
        class Provider:
            def __init__(self,winner): self.winner=winner
            def get(self,url,params=None):
                receipt={'sha256':'s','received_at_epoch':100}
                if 'gamma' in url:
                    return {'id':'12','outcomes':['Yes','No'],'outcomePrices':['1','0'],
                        'clobTokenIds':['123456','2'],'conditionId':'c','closed':True},receipt
                return {'condition_id':'c','closed':True,'tokens':[{'outcome':'Yes','token_id':'123456','winner':self.winner},
                    {'outcome':'No','token_id':'2','winner':False}]},receipt
        unresolved,_=venue_status(Provider(False),MARKET)
        resolved,_=venue_status(Provider(True),MARKET)
        self.assertIsNone(unresolved['result'])
        self.assertEqual(resolved['result'],'yes')

    def test_old_poly_windows_request_explicit_coarse_grain(self):
        class Provider:
            requests=0
            def get(self,url,params):
                self.params=params
                return {'data':[],'pagination':{'has_more':False}}, {'sha256':'s'}
        provider=Provider()
        points,_,grain=history(provider,MARKET,0,86400,100*86400)
        self.assertEqual(grain,10800)
        self.assertEqual(provider.params['bucket_seconds'],10800)
        self.assertEqual(points,[])


if __name__=='__main__': unittest.main()
