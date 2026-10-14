"""Bounded data views, explicit roles and safe source identity reuse."""
import base64
import copy
import hashlib
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.market_repricing.acquisition_contract import (
    body_contract, coverage, materialize_csv, saved_source, sheet_export)
from ForecastAgent.market_repricing.evidence_capture import candidates


SHEET = 'https://docs.google.com/spreadsheets/d/public_book/edit?gid=123#gid=123'


class AcquisitionContract(unittest.TestCase):
    def test_sheet_view_is_partial_even_when_parser_says_not_truncated(self):
        p={'content':'Date | Value\n12/16/2020 | 4', 'documents':[
            {'metadata':{'format':'html_table','row_count':100,'truncated':False}}]}
        q=body_contract(p,SHEET,{'state':'readable','usable_text':True,'qualifies_full_body':True})
        self.assertTrue(q['usable_text']);self.assertFalse(q['qualifies_full_body'])
        self.assertEqual(q['body_role'],'partial_data')
        self.assertEqual(q['data_coverage']['latest_observed_date'],'2020-12-16')
        self.assertFalse(q['target_coverage_verified'])

    def test_ambiguous_dates_stay_unknown(self):
        c=coverage({'content':'Date | Value\n1/2/2026 | 5'},SHEET)
        self.assertTrue(c['ambiguous_slash_date_order'])
        self.assertIsNone(c['latest_observed_date'])

    def test_short_numeric_notice_not_rejected(self):
        q=body_contract({'content':'2026-10-14: 5.1%'},'https://example.gov/rate',
            {'state':'thin','usable_text':True,'qualifies_full_body':True})
        self.assertTrue(q['usable_text']);self.assertTrue(q['qualifies_full_body'])
        self.assertEqual(q['data_coverage']['latest_observed_date'],'2026-10-14')

    def test_blocked_data_cannot_become_partial_usable(self):
        q=body_contract({'content':'Access denied'},SHEET,
            {'state':'blocked','usable_text':False,'qualifies_full_body':False})
        self.assertFalse(q['usable_text']);self.assertFalse(q['qualifies_full_body'])

    def test_diagnostics_and_saved_capture_not_mutated(self):
        p={'content':'2026-10-14: 5.1%'};d={'state':'thin','usable_text':True}
        before=copy.deepcopy((p,d));body_contract(p,SHEET,d)
        self.assertEqual((p,d),before)

    def test_data_export_requires_unambiguous_exact_public_identity(self):
        self.assertEqual(sheet_export(SHEET),'https://docs.google.com/spreadsheets/d/public_book/export?format=csv&gid=123')
        for u in (SHEET.replace('#gid=123','#gid=456'),SHEET.replace('docs.google.com','evil.example'),
                  SHEET.replace('https://','http://'),'https://docs.google.com/spreadsheets/d/public_book/edit'):
            self.assertIsNone(sheet_export(u))

    def test_share_intent_not_original_even_when_it_repeats_a_news_headline(self):
        url='https://api.whatsapp.com/send?text=Official+report'
        q=body_contract({'content':'Share on WhatsApp. Official report. '*20},url,
            {'state':'readable','usable_text':True,'qualifies_full_body':True})
        self.assertFalse(q['usable_text']);self.assertEqual(q['body_role'],'sharing_action')
        self.assertEqual(candidates({'searches':[{'results':[{'url':url}]}]}),[])
        normal=body_contract({'content':'A newspaper discusses WhatsApp sharing.'},'https://news.example/story',
            {'state':'readable','usable_text':True,'qualifies_full_body':True})
        self.assertTrue(normal['usable_text'])

    def test_export_has_parent_capture_binding(self):
        b={'pages':{SHEET:{'url':SHEET,'content':'Table', 'sha256':'saved-parent','retrieved_at_utc':'2026-10-14T00:00:00Z'}}}
        row=next(r for r in candidates(b) if 'data_export' in r['roles'])
        self.assertEqual(row['parent_source_sha256'],'saved-parent')
        self.assertEqual(row['parent_url'],SHEET)

    def test_verified_mnd_document_alias_reused(self):
        u='https://www.mnd.gov.tw/en/News/PLAAct/87977'
        b={'pages':{u:{'url':u,'content':'PLA activities: 4 aircraft entered ADIZ.', 'retrieved_at_utc':'2026-10-14T00:00:00Z'}}}
        p,proof=saved_source(b,u.replace('/en/','/En/'))
        self.assertEqual(proof['document_id'],'mnd:en:plaact:87977')
        self.assertEqual(proof['source_capture_at'],p['retrieved_at_utc'])
        self.assertIsNone(saved_source(b,u.replace('87977','87978'))[0])

    def test_mnd_block_not_reused_as_bulletin(self):
        u='https://www.mnd.gov.tw/en/News/PLAAct/87977'
        self.assertIsNone(saved_source({'pages':{u:{'url':u,'content':'Access denied'}}},u.replace('/en/','/En/'))[0])

    def test_other_path_case_and_query_remain_distinct(self):
        u='https://example.org/Report?a=1'
        b={'pages':{u:{'url':u,'content':'Public report'}}}
        for v in (u.replace('Report','report'),u.replace('a=1','a=2')):
            self.assertIsNone(saved_source(b,v)[0])
        self.assertIsNotNone(saved_source(b,u+'#section')[0])

    def page(self,raw):
        return {'content':'legacy parser view','content_type':'text/csv','raw_response_base64':base64.b64encode(raw).decode(),
                'sha256':hashlib.sha256(raw).hexdigest(),'retrieved_at_utc':'2026-10-14T00:00:00Z'}

    def test_csv_projection_preserves_complete_original_and_row_coordinates(self):
        raw=('Date,Value\n'+''.join('2026-10-14,%s\n'%i for i in range(600))).encode()
        old=self.page(raw);p=materialize_csv(old,sheet_export(SHEET))
        self.assertEqual(p['raw_response_base64'],old['raw_response_base64'])
        self.assertEqual(p['sha256'],old['sha256']);self.assertEqual(p['retrieved_at_utc'],old['retrieved_at_utc'])
        c=p['data_export_coverage'];self.assertEqual(c['source_rows_parsed'],601)
        self.assertIn(601,c['original_row_numbers']);self.assertTrue(p['content_truncated'])
        self.assertFalse(c['selected_rows_guaranteed_recent']);self.assertFalse(c['full_dataset_verified'])
        self.assertEqual(old['content'],'legacy parser view')

    def test_small_csv_no_invented_truncation(self):
        p=materialize_csv(self.page(b'Date,Value\n2026-10-14,4\n'),sheet_export(SHEET))
        self.assertFalse(p['content_truncated'])
        self.assertEqual(p['data_export_coverage']['coverage_status'],'complete_captured_csv')

    def test_csv_hash_mismatch_rejected(self):
        p=self.page(b'Date,Value\n2026-10-14,4');p['sha256']='bad'
        with self.assertRaises(ValueError):materialize_csv(p,sheet_export(SHEET))

    def test_csv_route_cannot_parse_login_html(self):
        p=self.page(b'<html>Please sign in</html>')
        self.assertEqual(materialize_csv(p,sheet_export(SHEET)),p)

    def test_supplement_reuses_alias_and_resume_performs_no_requests(self):
        from ForecastAgent.market_repricing.evidence_capture import recover
        u='https://www.mnd.gov.tw/en/News/PLAAct/87977'
        alias=u.replace('/en/','/En/')
        novel=u.replace('87977','87988')
        b={'request':{},'pages':{u:{'url':u,'content':'PLA activities: 4 aircraft entered ADIZ.',
               'retrieved_at_utc':'2026-10-14T00:00:00Z'}},
           'searches':[{'results':[{'url':alias},{'url':novel}]}]}
        original=copy.deepcopy(b);calls=[]
        def capture(method,url,*args,**kwargs):
            calls.append(url)
            return {'url':url,'content':'PLA activities: a new official report.', 'retrieved_at_utc':'2026-10-14T01:00:00Z'}
        def diag(page,url):return {'state':'readable','usable_text':True,'qualifies_full_body':True}
        with tempfile.TemporaryDirectory() as folder, patch('ForecastAgent.market_repricing.evidence_capture.quality',diag), patch(
            'ForecastAgent.market_repricing.materials.prepare',side_effect=lambda child:(child,{'external_full_body_count':len(child['pages'])})):
            child,report=recover(b,folder,capture=capture)
            self.assertEqual(calls,[novel]);self.assertEqual(report['physical_capture_reservations'],1)
            self.assertEqual(report['saved_source_reuse'][0]['requested_url'],alias)
            self.assertEqual(report['saved_source_reuse'][0]['source_capture_at'],'2026-10-14T00:00:00Z')
            recover(b,folder,capture=capture);self.assertEqual(calls,[novel])
        self.assertEqual(b,original)


if __name__=='__main__':unittest.main()
