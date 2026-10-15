"""Evidence/route/batch isolation, absent states and safe read-only Web output."""
import hashlib
import json
from pathlib import Path
import tempfile
import io
import tarfile
import unittest

from ForecastAgent.market_store.store import connect,digest,encoded,ingest_summary
from ForecastAgent.market_store.web_materials import hydrate,fingerprint,packet,clean
from ForecastAgent.market_store.web_read import open_readonly,read_body,read_catalog,read_task,read_history,read_index,read_watermark,publish_snapshot
from test_store import AT,KEY,MARKET


class WebContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)/'store'
        self.cache=Path(self.tmp.name)/'cache';self.cache.mkdir()
        self.db=connect(self.root/'market.sqlite3')
        self.question={'market_research_key':KEY,'question':MARKET['question'],'resolution_criteria':MARKET['rules']}
        self.body='Original source with a numeric zero: 0.\n[PDF page 2]\nA\tB\n1\t2'
        self.url='https://example.org/report.pdf'
        self.body_sha=hashlib.sha256(self.body.encode()).hexdigest()
        self.bundle={'request':self.question,'pages':{self.url:{'content':self.body,'content_sha256':self.body_sha,
            'retrieved_at_utc':AT}},'gaps':['exact_publication_time_not_recorded']}
        source={'source_id':'score-id','url':self.url,'body_sha256':self.body_sha}
        self.state={'question':self.question,'sources':[source],
            'evidence':[{'source_id':'score-id','evidence_id':'E1','text':self.body[:15],'start':0,'end':15}]}
        result={'status':'completed','eventual_probability_yes':0.0,'price_means':{'24':0.2},
            'state_sha256':digest(self.state),'response_sha256':'saved-response','sealed_at':AT}
        self.task={'id':KEY,'market':MARKET,'status':'completed','enrolled_at':AT,'analysis_stage':'analysis',
            'forecast':{'sealed_at':AT,'anchor_at':AT,'arms':{'original':result}}}
        summary={'generated_at':AT,'sources':{'s':{'state.json':{'tasks':[self.task]}}}}
        with self.db: ingest_summary(self.db,summary,{'stream':'market-worker','run_id':'1'})
        self.save('analysis-input.json',self.bundle)
        self.save('material-integrity.json',{'bundle_sha256':digest(self.bundle)})
        self.save('analysis/original/state.json',self.state)
        self.save('analysis/original/result.json',result)

    def save(self,path,value):
        raw=json.dumps(value,ensure_ascii=False,indent=2).encode()
        sha=hashlib.sha256(raw).hexdigest();(self.cache/(sha+'.json')).write_bytes(raw)
        with self.db:
            self.db.execute('INSERT INTO material_refs VALUES(?,?,?,?,?,?,?)',
                (digest(path),KEY,'tasks/'+digest(KEY)+'/'+path,sha,len(raw),'{}','remote_archive_reference'))

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_exact_model_evidence_zero_and_source_binding(self):
        report=hydrate(self.db,self.root,[self.cache])
        self.assertEqual(report['packets'][0]['status'],'worker_material_available')
        view=read_task(self.db,KEY)
        self.assertEqual(view['revisions'][0]['forecast']['arms']['original']['eventual_probability_yes'],0.0)
        m=view['revisions'][0]['materials']
        self.assertEqual(m['evidence_by_arm']['original'][0]['text'],self.body[:15])
        self.assertEqual(m['evidence_by_arm']['mapped'],[])
        self.assertNotEqual(m['sources'][0]['id'],m['sources_by_arm']['original'][0]['source_id'])
        self.assertEqual(read_body(self.db,m['sources'][0]['display_sha256']),self.body.encode())
        self.assertIsNone(view['opening_quote'])
        self.assertEqual(next(p for p in view['prices'] if p['hours']==120)['status'],'not_scheduled')

    def test_missing_material_retains_forecast_without_borrowing(self):
        for p in self.cache.glob('*'): p.unlink()
        report=hydrate(self.db,self.root,[self.cache])
        self.assertEqual(report['packets'][0]['status'],'pack_not_synced')
        view=read_task(self.db,KEY)
        self.assertIsNone(view['revisions'][0]['materials'])
        self.assertEqual(view['effective_model_status'],'completed')

    def test_changed_forecast_does_not_reuse_earlier_packet(self):
        hydrate(self.db,self.root,[self.cache])
        changed=json.loads(json.dumps(self.task));changed['forecast']['arms']['original']['eventual_probability_yes']=0.9
        summary={'generated_at':'2026-10-15T03:00:00Z','sources':{'s':{'state.json':{'tasks':[changed]}}}}
        with self.db: ingest_summary(self.db,summary,{'stream':'market-worker','run_id':'2'})
        view=read_task(self.db,KEY)
        self.assertIsNone(view['revisions'][0]['materials'])
        self.assertEqual(view['revisions'][0]['material_status'],'pack_not_synced')

    def test_hash_corruption_is_not_no_readable_material(self):
        for p in self.cache.glob('*'):
            if json.loads(p.read_bytes()).get('pages'): p.write_bytes(b'{}')
        report=hydrate(self.db,self.root,[self.cache])
        self.assertEqual(report['packets'][0]['status'],'integrity_failed')

    def test_bad_route_does_not_hide_other_verified_route(self):
        bad={**self.state,'sources':[{'source_id':'score-id','url':'https://wrong.example','body_sha256':self.body_sha}]}
        statuses={'original':{'status':'model_input_verified'},'mapped':{'status':'model_input_verified'}}
        value=packet(self.bundle,MARKET,{'original':self.state,'mapped':bad},statuses,{}, {})
        self.assertTrue(value['evidence_by_arm']['original'])
        self.assertEqual(value['evidence_by_arm']['mapped'],[])
        self.assertEqual(value['route_statuses']['mapped']['status'],'integrity_failed')

    def test_no_secret_or_private_path_in_display_payload(self):
        value=clean({'headers':{'Authorization':'Bearer hidden'},'text':'Bearer hidden C:\\private\\saved.json',
            'url':'https://example.org/a?api_key=hidden&date=2026','graph':{'api_key':'hidden','weight':0}})
        self.assertNotIn('headers',value)
        self.assertNotIn('api_key',value['graph'])
        self.assertEqual(value['graph']['weight'],0)
        self.assertNotIn('hidden',json.dumps(value))
        self.assertNotIn('C:\\private',json.dumps(value))

    def test_readonly_connection_and_document_corruption(self):
        hydrate(self.db,self.root,[self.cache])
        reader=open_readonly(self.root)
        with self.assertRaises(Exception): reader.execute("UPDATE markets SET question='edited'")
        self.assertEqual(len(read_catalog(reader)['tasks']),1)
        reader.close()
        sha=self.db.execute('SELECT display_sha256 FROM web_documents').fetchone()[0]
        with self.db:self.db.execute("UPDATE web_documents SET text='corrupt' WHERE display_sha256=?",(sha,))
        with self.assertRaises(ValueError):read_body(self.db,sha)

    def test_compact_index_and_stable_polling_watermark(self):
        hydrate(self.db,self.root,[self.cache])
        first=read_watermark(self.db)
        index=read_index(self.db)
        self.assertEqual(first,read_watermark(self.db))
        task=index['tasks'][0]
        self.assertEqual(task['readable_sources_count'],1)
        self.assertIsNone(task['revisions'][0]['materials'])
        self.assertFalse(task['revisions'][0]['materials_included'])
        self.assertEqual(task['local_material_status'],'worker_material_available')

    def test_history_kinds_grains_and_pagination_do_not_mix(self):
        from ForecastAgent.market_store.store import append
        with self.db:
            for resolution,at,price in ((60,10,0.3),(60,20,0.4),(300,10,0.2)):
                append(self.db,'point',KEY,{'kind':'provider_history_price','point_at':at,
                    'resolution_seconds':resolution,'price':price,'response_sha256':'s','run_id':'1'},AT)
        first=read_history(self.db,KEY,limit=1)
        self.assertEqual(first['selected_resolution_seconds'],60)
        self.assertEqual(first['points'][0]['price'],0.4)
        second=read_history(self.db,KEY,limit=1,before=first['pagination']['next_before'])
        self.assertEqual(second['points'][0]['price'],0.3)
        coarse=read_history(self.db,KEY,resolution=300)
        self.assertEqual(len(coarse['points']),1)
        self.assertEqual(coarse['points'][0]['price'],0.2)

    def test_export_matches_existing_body_and_price_loader_names(self):
        hydrate(self.db,self.root,[self.cache])
        output=Path(self.tmp.name)/'export'
        result=publish_snapshot(self.db,output)
        self.assertEqual(result['markets'],1)
        data=json.loads((output/'market-research.json').read_bytes())
        self.assertEqual(data['schema'],'prediction-market-web-v1')
        index=json.loads((output/'market-price-index.json').read_bytes())
        item=index['markets'][KEY]
        self.assertEqual(hashlib.sha256((output/item['path']).read_bytes()).hexdigest(),item['sha256'])
        source=data['tasks'][0]['revisions'][0]['materials']['sources'][0]
        body=output/source['body_ref'].removeprefix('data/')
        self.assertEqual(hashlib.sha256(body.read_bytes()).hexdigest(),source['display_sha256'])

    def test_local_pack_fallback_is_verified_and_never_extracts_paths(self):
        refs=list(self.db.execute('SELECT sha256 FROM material_refs'))
        path=Path(self.tmp.name)/'task.tar.xz'
        with tarfile.open(path,'w:xz') as archive:
            for row in refs:
                raw=(self.cache/(row[0]+'.json')).read_bytes()
                info=tarfile.TarInfo('objects/'+row[0]);info.size=len(raw)
                archive.addfile(info,io.BytesIO(raw))
        raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        target=self.root/'objects'/sha[:2]/sha;target.parent.mkdir(parents=True);target.write_bytes(raw)
        with self.db:
            self.db.execute('INSERT INTO pack_locations VALUES(?,?,?,?,?,?)',('p',KEY,sha,'packs/p.tar.xz',AT,'{}'))
        for p in self.cache.glob('*'):p.unlink()
        result=hydrate(self.db,self.root)
        self.assertEqual(result['packets'][0]['status'],'worker_material_available')


if __name__=='__main__':unittest.main()
