import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.engineering.common import connect, digest, encoded
from ForecastAgent.engineering.snapshots import packet, inventory
from ForecastAgent.engineering.native import sync
from ForecastAgent.competition_store import store as competition_store
from ForecastAgent.market_store import store as market_store
from ForecastAgent.market_store.web_materials import install
from .fixtures import CLOCK, competition, bundle


def market(root):
    root=Path(root)
    path=root/'prediction_markets/store/market.sqlite3'
    native=market_store.connect(path);install(native)
    value=bundle()
    value['request']['market_research_key']='polymarket:123'
    raw=encoded(value);sha=hashlib.sha256(raw).hexdigest()
    object_path=root/'blobs/sha256'/sha[:2]/sha
    object_path.parent.mkdir(parents=True);object_path.write_bytes(raw)
    artifact_path='tasks/'+digest('polymarket:123')+'/analysis-input.json'
    material={'material_sha256':digest(value),'provenance':{'kind':'verified_native_objects','material_path':'analysis-input.json'}}
    with native:
        native.execute('INSERT INTO markets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('polymarket:123','polymarket','123','123','polymarket:event','condition',CLOCK,'frozen',
             value['request']['question'],'https://example.org',value['request']['resolution_criteria'],'rules'))
        native.execute('INSERT INTO material_refs VALUES(?,?,?,?,?,?,?)',
            ('ref','polymarket:123',artifact_path,sha,len(raw),'{}','verified_local_object'))
        native.execute('INSERT INTO web_packets VALUES(?,?,?,?,?,?,?,?,?)',
            ('batch','polymarket:123','initial_worker','original_worker','frozen','worker_material_available',digest(value),CLOCK,encoded({'materials':material}).decode()))
    native.close()
    return path,object_path


class NativeAdapters(unittest.TestCase):
    def test_market_reads_exact_raw_original_without_cache_writes(self):
        with tempfile.TemporaryDirectory() as root:
            path,original=market(root)
            before={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(root).rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm'))}
            p=packet('market',path,'batch','polymarket:123')
            self.assertEqual(p['sources'][0]['text'],next(iter(bundle()['pages'].values()))['content'])
            after={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(root).rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm'))}
            self.assertEqual(before,after)
            self.assertNotIn('FORBIDDEN',json.dumps(p))

    def test_market_missing_or_changed_object_never_substitutes_web_display(self):
        for missing in (True,False):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as root:
                path,original=market(root)
                if missing:original.unlink()
                else:original.write_text('corrupt')
                with self.assertRaises((ValueError,FileNotFoundError)):
                    packet('market',path,'batch','polymarket:123')

    def test_inventory_names_batches_but_does_not_choose_latest(self):
        with tempfile.TemporaryDirectory() as root:
            path,bid=competition(root)
            items=inventory('competition',path)
            self.assertEqual(items[0]['batch_id'],bid)
            self.assertEqual(items[0]['task_key'],'metaculus:123')

    def test_acceptance_requires_payload_bound_platform_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            path,_=competition(root)
            native=competition_store.connect(path)
            with native:competition_store.task(native,'123',{'stage':'accepted'},CLOCK,'s')
            native.close()
            db=connect(Path(root)/'engineering')
            try:
                report=sync(db,'competition',path)
                self.assertEqual(report['tasks']['metaculus:123']['state'],'submission_unknown')
                self.assertEqual(report['tasks']['metaculus:123']['action'],'readback_only')
                native=competition_store.connect(path)
                payload={'question':'123','probability_yes':.4}
                with native:
                    competition_store.candidate(native,'123',{'payload':payload},CLOCK,'s')
                    competition_store.receipt(native,'123',{'payload':payload,'status':'accepted','confirmed_at_utc':CLOCK},CLOCK,'s')
                native.close()
                report=sync(db,'competition',path)
                self.assertEqual(report['tasks']['metaculus:123']['state'],'accepted')
                self.assertEqual(sync(db,'competition',path)['new_observations'],0)
            finally:db.close()
