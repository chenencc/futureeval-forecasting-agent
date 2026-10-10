"""Acquisition-side discovery, provenance and shared request-budget tests."""
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from ForecastAgent.tools.intelligence_box.core import Toolbox
from ForecastAgent.tools.intelligence_box.discovery import parse_discovery


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)

    def source(self,kind='sitemap',offset=0,limit=2):
        return {'final_url':'https://www.fec.gov/index.xml','discovery_hosts':['www.fec.gov'],
                'discovery_parameters':{'kind':kind,'profile_id':None,'offset':offset,'limit':limit}}

    def box(self,raw,status=200,cap=3):
        calls=[]
        def fetch(url,limit):
            calls.append(url)
            if url.endswith('index.xml'):
                return {'raw':raw,'status':200,'content_type':'application/xml','final_url':url}
            return {'raw':b'<html><h1>Official announcement</h1><p>'+b'Actual published original text. '*20+b'</p></html>',
                    'status':status,'content_type':'text/html','final_url':url}
        box=Toolbox(self.temp.name,max_requests=cap,fetcher=fetch); self.addCleanup(box.close)
        return box,calls

    def test_sitemap_index_is_not_automatic_recursion(self):
        raw=b'<sitemapindex><sitemap><loc>https://www.fec.gov/child.xml</loc><lastmod>2026-10-01</lastmod></sitemap></sitemapindex>'
        box,calls=self.box(raw)
        c=box.discover('https://www.fec.gov/index.xml','sitemap')
        self.assertEqual(len(calls),1)
        self.assertEqual(c['records'][0]['target_kind'],'sitemap')
        with self.assertRaisesRegex(ValueError,'explicit discover'): box.acquire_link(c['id'],0)
        self.assertEqual(box.budget()['reserved_attempts'],1)

    def test_download_original_shared_cap_and_provenance(self):
        raw=b'<urlset><url><loc>https://www.fec.gov/page</loc></url></urlset>'
        box,calls=self.box(raw,cap=2)
        c=box.discover('https://www.fec.gov/index.xml','sitemap')
        d=box.acquire_link(c['id'],0)
        self.assertEqual(d['status'],'usable')
        self.assertEqual(d['source_binding']['parent_raw_sha256'],c['raw_sha256'])
        self.assertTrue((Path(self.temp.name)/d['raw_path']).is_file())
        self.assertEqual(box.acquire_link(c['id'],0)['id'],d['id'])
        self.assertEqual(len(calls),2)
        with self.assertRaises(RuntimeError): box.read('https://www.fec.gov/another')

    def test_failed_download_keeps_parent_binding(self):
        box,calls=self.box(b'<urlset><url><loc>https://www.fec.gov/page</loc></url></urlset>',status=403)
        c=box.discover('https://www.fec.gov/index.xml','sitemap'); d=box.acquire_link(c['id'],0)
        self.assertEqual(d['status'],'failed')
        self.assertEqual(d['source_binding']['parent_capture_id'],c['id'])
        self.assertEqual(len(calls),2)

    def test_gzip_pagination_dedup_and_cross_host_rejection(self):
        raw=b'<urlset><url><loc>https://www.fec.gov/a#x</loc></url><url><loc>https://www.fec.gov/a#y</loc></url><url><loc>https://evil.example/a</loc></url><url><loc>https://www.fec.gov/b?token=private</loc></url></urlset>'
        rows,coverage=parse_discovery(self.source(limit=1),gzip.compress(raw))
        self.assertEqual(rows[0]['url'],'https://www.fec.gov/a')
        self.assertEqual(coverage['native_metadata']['next_offset'],1)
        self.assertEqual(coverage['native_metadata']['duplicates_removed'],1)
        rows,_=parse_discovery(self.source(offset=1),raw)
        self.assertFalse(rows[0]['eligible']); self.assertFalse(rows[1]['eligible'])

    def test_atom_alternate_link_and_empty_feed(self):
        raw=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Notice</title><link rel="self" href="/self"/><link rel="alternate" href="/notice"/><updated>2026-10-01</updated></entry></feed>'
        rows,_=parse_discovery(self.source('rss'),raw)
        self.assertEqual(rows[0]['url'],'https://www.fec.gov/notice')
        self.assertEqual(rows[0]['updated'],'2026-10-01')
        self.assertEqual(parse_discovery(self.source('rss'),b'<rss><channel/></rss>')[0],[])

    def test_dtd_wrong_envelope_and_validation_before_budget(self):
        for raw in (b'<!DOCTYPE a><urlset/>',b'<html>blocked</html>'):
            with self.assertRaises(ValueError): parse_discovery(self.source(),raw)
        box,calls=self.box(b'<urlset/>')
        for kwargs in ({'kind':'unknown'},{'kind':'ir'},{'kind':'rss','limit':True}):
            with self.assertRaises(ValueError): box.discover('https://www.fec.gov/index.xml',**kwargs)
        self.assertEqual(len(calls),0)

    def test_tampered_candidate_and_original_rejected(self):
        box,calls=self.box(b'<urlset><url><loc>https://www.fec.gov/a</loc></url></urlset>')
        c=box.discover('https://www.fec.gov/index.xml','sitemap')
        p=Path(self.temp.name)/'captures'/c['id']/'capture.json'
        c['records'][0]['url']='https://www.fec.gov/injected'; p.write_text(json.dumps(c))
        with self.assertRaisesRegex(ValueError,'match'): box.acquire_link(c['id'],0)
        self.assertEqual(len(calls),1)

    def test_ir_profile_entity_and_asset_host(self):
        source=self.source('ir'); source['discovery_parameters']['profile_id']='tesla_ir'
        source['final_url']='https://ir.tesla.com/'; source['discovery_hosts']=['ir.tesla.com','assets-ir.tesla.com']
        raw=b'<html><a href="https://assets-ir.tesla.com/Q2.pdf">Quarterly shareholder update</a></html>'
        rows,_=parse_discovery(source,raw)
        self.assertEqual(rows[0]['cik'],'0001318605'); self.assertTrue(rows[0]['eligible'])

    def test_binary_file_download_is_distinct_from_readability(self):
        box,calls=self.box(b'<urlset/>')
        box.fetcher=lambda url,limit:{'raw':b'PK\x03\x04saved original','status':200,
            'content_type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','final_url':url}
        c=box.read('https://www.fec.gov/document.docx')
        self.assertEqual(c['status'],'captured_unparsed')
        self.assertTrue(c['quality']['original_download_complete'])
        self.assertFalse(c['quality']['readable_document'])
        self.assertTrue(box.read('https://www.fec.gov/document.docx')['cache_hit'])
        self.assertEqual(box.budget()['reserved_attempts'],1)

    def test_html_base_and_unrelated_issuer_links(self):
        source=self.source('ir'); source['discovery_parameters']['profile_id']='microsoft_ir'
        source['final_url']='https://www.microsoft.com/investor/reports/ar25/download-center/index.html'
        source['discovery_hosts']=['www.microsoft.com']
        raw=b'<html><head><base href="https://www.microsoft.com/investor/reports/ar25/"/></head><a href="downloads/file.pdf">Download financial report</a><a href="https://www.microsoft.com/en-us/download">Free downloads</a></html>'
        rows,_=parse_discovery(source,raw)
        self.assertEqual(rows[0]['url'],'https://www.microsoft.com/investor/reports/ar25/downloads/file.pdf')
        self.assertTrue(rows[0]['eligible']);self.assertFalse(rows[1]['eligible'])


if __name__=='__main__': unittest.main()
