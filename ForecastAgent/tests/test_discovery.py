"""Observed URL discovery, bounded repair and PDF failure regressions."""
import base64
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.supplement.discovery import discover
from ForecastAgent.readers.pdf_diagnostics import diagnose_pdf
from ForecastAgent.readers.loader import load_response
from ForecastAgent.supplement.stage import run

class DiscoveryTests(unittest.TestCase):
    def bundle(self):
        return {'request':{'question':'Will Acme revenue reach 2026 target?',
          'resolution_criteria':'According to [issuer release](https://issuer.example/releases/acme-2026)'},
          'pages':{'https://issuer.example/':{'links':['https://issuer.example/releases/acme-2026','https://issuer.example/privacy']}},
          'searches':[{'results':[{'url':'https://news.example/acme-2026','title':'Acme revenue 2026'},
            {'url':'https://news.example/other','title':'Different company'}]}]}
    def test_primary_and_alternate_observed_only(self):
        plan=discover(self.bundle(),[{'url':'https://issuer.example/','category':'index_or_banner'}])
        candidates=plan['gaps'][0]['candidates']
        self.assertEqual(candidates[0]['kind'],'rule_primary_source')
        self.assertEqual(candidates[1]['kind'],'public_alternative')
        self.assertFalse(any('privacy' in c['url'] or 'other' in c['url'] for c in candidates))
        self.assertFalse(plan['urls_synthesized'])
        self.assertFalse(candidates[1]['equivalence_verified'])
    def test_private_and_already_readable_sources_excluded(self):
        b=self.bundle();b['searches'][0]['results'].append({'url':'http://127.0.0.1/acme','title':'Acme revenue'})
        b['pages']['https://issuer.example/releases/acme-2026']={'content':'Acme revenue published measurement. '*40}
        result=discover(b,[{'url':'https://issuer.example/','category':'access_restricted'}])
        urls=[c['url'] for c in result['gaps'][0]['candidates']]
        self.assertNotIn('http://127.0.0.1/acme',urls)
        self.assertNotIn('https://issuer.example/releases/acme-2026',urls)
    def test_no_topic_overlap_no_guessed_path(self):
        b=self.bundle();b['request']={'question':'Unrelated subject'}
        result=discover(b,[{'url':'https://issuer.example/','category':'missing_url'}])
        self.assertEqual(result['gaps'][0]['status'],'no_observed_alternative')
    def test_shared_http_cap_resume_and_parent_preservation(self):
        b=self.bundle();b['pages']['https://issuer.example/'].update(content='',body_diagnostics={'state':'navigation_shell','usable_text':False})
        b['fetch_attempts']=[{'url':'https://issuer.example/','status':'failed','detail':'HTTP Error 403'}]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'parent.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('campaign.json',json.dumps({'tasks':{'1':{'status':'closed_with_gaps'}}}))
                z.writestr('tasks/1/bundle.json',json.dumps(b))
            before=archive.read_bytes()
            response={'content':'Acme revenue measured official release. '*40,'url':'https://issuer.example/releases/acme-2026'}
            with patch('ForecastAgent.supplement.stage.fetch_document',return_value=response) as fetch:
                run(archive,root/'out',['1'],network=True,http_limit=1)
                run(archive,root/'out',['1'],network=True,http_limit=1)
                self.assertEqual(fetch.call_count,1)
                self.assertEqual(fetch.call_args.args[0],'https://issuer.example/releases/acme-2026')
            self.assertEqual(before,archive.read_bytes())
            child=json.loads((root/'out/tasks/1/supplement.json').read_text())
            self.assertTrue(child['attempts'][0]['discovery_provenance'])
            self.assertTrue(any(r['url']=='https://issuer.example/' for r in child['remaining_gaps']))

class PDFDiagnosisTests(unittest.TestCase):
    def blank(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        stream=io.BytesIO();writer.write(stream);return stream.getvalue()
    def test_non_pdf_and_malformed_are_distinct(self):
        self.assertEqual(diagnose_pdf(b'<html>Denied</html>','application/pdf')['state'],'non_pdf_response')
        self.assertEqual(diagnose_pdf(b'%PDF-1.7 invalid','application/pdf')['state'],'malformed_or_truncated_pdf')
    def test_blank_pdf_diagnosis_saved_with_failed_raw(self):
        raw=self.blank()
        result=load_response({'raw':raw,'url':'https://issuer.example/a.pdf','final_url':'https://issuer.example/a.pdf','content_type':'application/pdf'},retrieved_at=None,preserve_raw_on_failure=True)
        self.assertEqual(result['pdf_diagnosis']['state'],'no_text_layer_in_sample')
        self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(base64.b64decode(result['raw_response_base64']),raw)
        self.assertFalse(result['pdf_diagnosis']['ocr_performed'])
    def test_rotated_text_is_recovered_without_ocr(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(width=400,height=400)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 0 1 -1 0 100 100 Tm (Official release 2026) Tj ET')
        page[NameObject('/Contents')]=stream
        output=io.BytesIO();writer.write(output)
        result=load_response({'raw':output.getvalue(),'url':'https://issuer.example/r.pdf','final_url':'https://issuer.example/r.pdf','content_type':'application/pdf'},retrieved_at=None,preserve_raw_on_failure=True)
        self.assertIn('Official release 2026',result['content'])
        self.assertEqual(result['documents'][0]['metadata']['extraction_method'],'pypdf_plain_fallback')

    def test_encrypted_pdf(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('secret')
        stream=io.BytesIO();writer.write(stream)
        self.assertEqual(diagnose_pdf(stream.getvalue())['state'],'encrypted_pdf')

if __name__=='__main__':unittest.main()
