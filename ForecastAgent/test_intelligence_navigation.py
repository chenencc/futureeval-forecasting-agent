"""Saved-original navigation integrity, pagination and late-content recovery."""
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.tools.intelligence_box.navigation import navigate
from ForecastAgent.tools.intelligence_box.core import Toolbox, TOOL_DEFINITIONS


class NavigationTests(unittest.TestCase):
    def capture(self,raw,kind='text/html'):
        return {'id':'a'*36,'raw_sha256':hashlib.sha256(raw).hexdigest(),'http':{'content_type':kind},'status':'usable'}

    def test_late_section_and_literal_offsets(self):
        raw=('<h1>Intro</h1><p>'+'x'*160000+'</p><h2>Target</h2><p>EPS 1.25 (GAAP)</p>').encode()
        c=self.capture(raw)
        hit=navigate(c,raw,'search',query='EPS')['hits'][0]
        part=navigate(c,raw,'part',unit_id=hit['unit_id'])
        self.assertEqual(part['text'][hit['start']:hit['end']],'EPS')
        self.assertEqual(part['title'],'Target')
        self.assertEqual(part['network_requests'],0)

    def test_table_rows_spans_and_header_preserved(self):
        raw=b'<h2>USD millions</h2><table><tr><th colspan="2">2026</th></tr><tr><td>Revenue</td><td>42</td></tr><tr><td>Profit</td><td>3</td></tr></table><p>After table</p>'
        c=self.capture(raw)
        p=navigate(c,raw,'part',unit_id='table:1',row_start=1,max_rows=1)
        self.assertEqual(p['rows'][0][0]['text'],'Revenue')
        self.assertEqual(p['header_rows'][0][0]['colspan'],'2')
        self.assertEqual(p['next_row'],2)
        self.assertNotIn('After table',p['text'])
        self.assertIn('After table',navigate(c,raw,'part',unit_id='section:2')['text'])

    def test_outline_and_text_pagination(self):
        raw=b'<h1>A</h1><p>'+b'z'*350+b'</p><h2>B</h2><p>second</p>'
        c=self.capture(raw)
        a=navigate(c,raw,'outline',limit=1)
        self.assertEqual(a['next_offset'],1)
        p=navigate(c,raw,'part',unit_id='section:2',max_chars=100)
        q=navigate(c,raw,'part',unit_id='section:2',start=p['next_start'],max_chars=100)
        self.assertEqual(q['start'],100)

    def test_xml_exact_identity_late_content_and_dtd(self):
        raw=b'<Law><P id="section-138">Later clause</P></Law>'
        c=self.capture(raw,'application/xml')
        self.assertEqual(navigate(c,raw,'outline')['units'][0]['element_id'],'section-138')
        self.assertEqual(navigate(c,raw,'part',unit_id='xml:1')['text'],'Later clause')
        with self.assertRaises(ValueError): navigate(c,b'<!DOCTYPE a><a/>','outline')

    def test_compression_and_limits(self):
        raw=gzip.compress(b'<h1>Compressed</h1><p>needle</p>')
        c=self.capture(raw)
        self.assertEqual(navigate(c,raw,'search',query='needle')['status'],'found')
        for kwargs in ({'query':''},{'query':'x','limit':True},{'query':'x','max_pages':501}):
            with self.assertRaises(ValueError): navigate(c,raw,'search',**kwargs)
        c['http']['truncated']=True
        with self.assertRaises(ValueError): navigate(c,raw,'outline')
        c['http']={'content_type':'text/html','status':403}
        with self.assertRaises(ValueError): navigate(c,raw,'outline')

    def test_missing_search_hit_and_regex_literal(self):
        raw=b'<p>a.b aXb a.b</p>'
        c=self.capture(raw)
        self.assertTrue(navigate(c,raw,'search',query='a.b',limit=1)['hits_truncated'])
        self.assertEqual(navigate(c,raw,'search',query='absent')['status'],'not_found')
        self.assertEqual(navigate(c,raw,'part',unit_id='bad')['status'],'not_found')

    def test_dispatch_integrity_and_no_network(self):
        with tempfile.TemporaryDirectory() as root:
            box=Toolbox(root,max_requests=1,fetcher=lambda *a: self.fail('Unexpected network'))
            self.addCleanup(box.close)
            raw=b'<h1>Saved</h1><p>original</p>'
            c=self.capture(raw); folder=Path(root)/'captures'/c['id']; folder.mkdir(parents=True)
            c['raw_path']=str((folder/'response.raw').relative_to(root))
            (folder/'response.raw').write_bytes(raw)
            (folder/'capture.json').write_text(json.dumps(c))
            self.assertEqual(box.call('intelligence_search',{'capture_id':c['id'],'query':'original'})['status'],'found')
            self.assertEqual(box.budget()['reserved_attempts'],0)
            (folder/'response.raw').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'hash'): box.call('intelligence_outline',{'capture_id':c['id']})
            box.close()

    def test_pdf_page_bound_and_late_table_read(self):
        class Page:
            def extract_text(self): return 'Late revenue 42'
            def extract_tables(self): return [[['Metric','Value'],['Revenue','42']]]
        class PDF:
            pages=[Page() for _ in range(30)]
            def __enter__(self): return self
            def __exit__(self,*args): pass
        raw=b'%PDF-fake'; c=self.capture(raw,'application/pdf')
        with patch('pdfplumber.open',return_value=PDF()):
            self.assertTrue(navigate(c,raw,'outline',max_pages=20)['coverage']['scan_truncated'])
            p=navigate(c,raw,'part',unit_id='page:27',max_pages=30,max_rows=1)
            self.assertEqual(p['page'],27)
            self.assertTrue(p['page_tables'][0]['rows_truncated'])

    def test_tool_schema_names(self):
        names={x['function']['name'] for x in TOOL_DEFINITIONS}
        self.assertTrue({'intelligence_outline','intelligence_search','intelligence_part'}<=names)

    def test_preformatted_legal_headings_and_comment(self):
        raw=b'<html><!-- comment --><pre>Sec. 9. TOC entry\nSEC. 9. ACTUAL CLAUSE.\n    substantive line\nSEC. 10. NEXT.\nnew text</pre></html>'
        c=self.capture(raw)
        hit=navigate(c,raw,'search',query='substantive')['hits'][0]
        part=navigate(c,raw,'part',unit_id=hit['unit_id'])
        self.assertEqual(part['title'],'SEC. 9. ACTUAL CLAUSE.')
        self.assertNotIn('new text',part['text'])
        self.assertEqual(part['line_start'],2)


if __name__=='__main__': unittest.main()
