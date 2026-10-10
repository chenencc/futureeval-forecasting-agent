"""Integrity and failure semantics for the independent acquisition toolbox."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.tools.intelligence_box.core import Toolbox, Redirects, build_url, parse, public_url
from ForecastAgent.tools.intelligence_box.catalog import SOURCES
from ForecastAgent.tools.intelligence_box.profiles import inventory,resolve_profile,tables


class ToolboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def box(self, raw=b'{"items": []}', status=200, cap=3):
        def fetch(url, limit):
            return {"raw":raw,"status":status,"content_type":"application/json","final_url":url,"retry_after":"60" if status==429 else None}
        box=Toolbox(self.temp.name,max_requests=cap,fetcher=fetch)
        self.addCleanup(box.close)
        return box

    def test_empty_success_and_cache_preserves_time(self):
        box=self.box()
        first=box.fetch("uk_bills")
        second=box.fetch("uk_bills")
        self.assertEqual(first["status"],"empty")
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["captured_at_utc"],second["captured_at_utc"])
        self.assertEqual(box.budget()["reserved_attempts"],1)

    def test_html_shell_is_not_success_and_raw_is_saved(self):
        box=self.box(b'<html>Checking your browser</html>')
        result=box.fetch("uk_bills")
        self.assertEqual(result["status"],"failed")
        self.assertTrue((Path(self.temp.name)/result["raw_path"]).is_file())

    def test_failure_counts_and_retry_after_is_retained(self):
        box=self.box(status=429,cap=1)
        result=box.fetch("uk_bills")
        self.assertEqual(result["http"]["retry_after"],"60")
        with self.assertRaisesRegex(RuntimeError,"budget exhausted"): box.fetch("uk_bills")

    def test_interrupted_reservation_counts_on_resume(self):
        box=self.box(cap=1)
        with box.db: box.db.execute("INSERT INTO attempts VALUES ('old','url','time','reserved',NULL)")
        with self.assertRaises(RuntimeError): box.fetch("uk_bills")

    def test_cannot_change_existing_cap(self):
        self.box(cap=2)
        with self.assertRaisesRegex(ValueError,"cap cannot change"): Toolbox(self.temp.name,max_requests=3)

    def test_unknown_parameter_rejected_before_reservation(self):
        box=self.box()
        with self.assertRaises(ValueError): box.fetch("uk_bills",{"api_key":"secret"})
        self.assertEqual(box.budget()["reserved_attempts"],0)

    def test_path_injection_and_missing_entity_rejected(self):
        for params in ({},{"country":"../all","indicator":"SP.POP.TOTL"}):
            with self.assertRaises(ValueError): build_url("worldbank",params)

    def test_cache_hash_corruption_forces_new_capture(self):
        box=self.box()
        first=box.fetch("uk_bills")
        (Path(self.temp.name)/first["raw_path"]).write_bytes(b"corrupted")
        second=box.fetch("uk_bills")
        self.assertFalse(second["cache_hit"])
        self.assertEqual(box.budget()["reserved_attempts"],2)

    def test_empty_feed_valid_and_dtd_forbidden(self):
        records,_=parse(SOURCES["fed_news"],b'<rss><channel/></rss>')
        self.assertEqual(records,[])
        with self.assertRaises(ValueError): parse(SOURCES["fed_news"],b'<!DOCTYPE rss><rss/>')

    def test_pagination_and_null_statistics_retained(self):
        raw=json.dumps([{"total":2,"pages":2,"page":1},[{"date":"2020","value":None}]]).encode()
        records,coverage=parse(SOURCES["worldbank"],raw)
        self.assertTrue(coverage["more_available"])
        self.assertEqual(records[0]["date"],"2020")
        self.assertIsNone(records[0]["value"])

    def test_private_or_credentialed_urls_rejected(self):
        for url in ("http://example.com","https://user:pass@example.com","https://127.0.0.1"):
            with self.assertRaises(ValueError): public_url(url)

    def test_csv_shell_and_wrong_schema_rejected(self):
        with self.assertRaises(ValueError): parse(SOURCES["fred_csv"],b"<html>Login</html>")
        with self.assertRaises((KeyError,ValueError)): parse(SOURCES["uk_bills"],b'{"error": "blocked"}')

    def test_document_interstitial_not_read_success(self):
        box=self.box()
        box.fetcher=lambda url,limit: {"raw":b'<html><body>Verify you are human. Access denied.</body></html>',"status":200,"content_type":"text/html","final_url":url}
        result=box.read("https://www.federalreserve.gov/test")
        self.assertEqual(result["status"],"failed")

    def test_document_requires_allowed_host_and_no_key_query(self):
        box=self.box()
        for url in ("https://unapproved.example/doc","https://www.federalreserve.gov/?api_key=hidden"):
            with self.assertRaises(ValueError): box.read(url)
        self.assertEqual(box.budget()["reserved_attempts"],0)

    def test_unapproved_redirect_blocked_before_follow(self):
        with patch("ForecastAgent.tools.intelligence_box.core.public_url"):
            with self.assertRaisesRegex(ValueError,"Redirect host"):
                Redirects({"approved.example"}).redirect_request(None,None,302,"",{},"https://other.example/file")

    def test_missing_cached_body_recovers_with_new_attempt(self):
        box=self.box()
        first=box.fetch("uk_bills")
        (Path(self.temp.name)/first["raw_path"]).unlink()
        second=box.fetch("uk_bills")
        self.assertEqual(second["status"],"empty")
        self.assertEqual(box.budget()["reserved_attempts"],2)

    def test_sec_missing_contact_blocks_before_http(self):
        box=self.box()
        with patch.dict("os.environ",{},clear=True):
            result=box.fetch("sec_concept",{"cik":"0001318605","taxonomy":"us-gaap","tag":"Revenues"})
        self.assertEqual(result["status"],"configuration_required")
        self.assertEqual(box.budget()["reserved_attempts"],0)

    def test_sec_header_configuration_not_saved(self):
        box=self.box(json.dumps({"cik":1318605,"taxonomy":"us-gaap","tag":"Revenues","units":{"USD":[{"start":"2026-01-01","end":"2026-06-30","val":10,"filed":"2026-07-30","form":"10-Q","accn":"test"}]}}).encode())
        box.configuration={"SEC_USER_AGENT":"Offline fixture fixture@example.org"}
        result=box.fetch("sec_concept",{"cik":"0001318605","taxonomy":"us-gaap","tag":"Revenues"})
        self.assertEqual(result["status"],"usable")
        self.assertNotIn("fixture@example.org",json.dumps(result))
        self.assertEqual(result["records"][0]["_source_context"]["unit"],"USD")
        self.assertEqual(result["records"][0]["start"],"2026-01-01")

    def test_wrong_issuer_fails_and_preserves_raw(self):
        box=self.box(json.dumps({"cik":789019,"taxonomy":"us-gaap","tag":"Revenues","units":{}}).encode())
        box.configuration={"SEC_USER_AGENT":"Offline fixture fixture@example.org"}
        result=box.fetch("sec_concept",{"cik":"0001318605","taxonomy":"us-gaap","tag":"Revenues"})
        self.assertEqual(result["status"],"failed")
        self.assertIn("issuer CIK",result["error"]["message"])
        self.assertEqual(box.budget()["reserved_attempts"],1)

    def test_sec_columnar_integrity_and_history_gap(self):
        data={"cik":"0000789019","name":"Microsoft","filings":{"recent":{"accessionNumber":["a"],"form":["10-K"],"filingDate":["2026-07-30"]},"files":[{"name":"older.json"}]}}
        records,coverage=parse(SOURCES["sec_submissions"],json.dumps(data).encode())
        self.assertTrue(coverage["more_available"])
        self.assertEqual(records[0]["form"],"10-K")
        data["filings"]["recent"]["form"]=[]
        with self.assertRaises(ValueError): parse(SOURCES["sec_submissions"],json.dumps(data).encode())

    def test_detail_identity_mismatch_rejected(self):
        box=self.box(b'{"billId":999,"isAct":false,"currentStage":{"description":"2nd reading"}}')
        result=box.fetch("uk_bill_detail",{"bill_id":4038})
        self.assertEqual(result["status"],"failed")
        self.assertIn("identity",result["error"]["message"])

    def test_exact_ids_and_statute_version_validation(self):
        with self.assertRaises(ValueError): build_url("sec_submissions",{"cik":"TSLA"})
        with self.assertRaises(ValueError): build_url("uk_bill_detail",{"bill_id":"4.2"})
        with self.assertRaises(ValueError): build_url("federal_register_detail",{"document_number":"wrong"})
        url=build_url("uk_legislation_xml",{"type":"ukpga","year":2025,"number":18,"version":"enacted"})
        self.assertTrue(url.endswith("/2025/18/enacted/data.xml"))

    def test_clml_retains_provision_and_table_coordinates(self):
        raw=b'<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation" DocumentURI="http://www.legislation.gov.uk/ukpga/2025/18"><Primary><P1 id="section-1"><Text>This Act shall apply.</Text><Tabular><Table><tr><td>Metric</td><td>Value</td></tr></Table></Tabular></P1></Primary></Legislation>'
        records,coverage=parse(SOURCES["uk_legislation_xml"],raw)
        self.assertEqual(records[0]["provision_ids"],["section-1"])
        self.assertEqual(records[1]["element"],"Tabular")
        self.assertIn("<",records[1]["original_xml"])
        self.assertTrue(coverage["native_metadata"]["changes_and_commencement_not_interpreted"])
        with self.assertRaises(ValueError): parse(SOURCES["uk_legislation_xml"],b'<html>Blocked</html>')

    def test_profile_does_not_accept_another_issuer(self):
        with self.assertRaises(ValueError): resolve_profile("tesla_ir","https://www.microsoft.com/report")

    def test_link_context_retains_quarter_without_future_report_claim(self):
        raw=b'<table><tr><td>2026 Q3 Earnings Oct 21, 2026</td><td>No deck yet</td></tr><tr><td>2026 Q2</td><td><a href="https://assets-ir.tesla.com/q2.pdf">Download</a></td></tr></table>'
        result=inventory(raw,"https://ir.tesla.com/",limit=5)
        self.assertEqual(len(result["links"]),1)
        self.assertIn("2026 Q2",result["links"][0]["row_context"])
        self.assertFalse(result["full_archive_complete"])

    def test_saved_link_inspection_consumes_no_request(self):
        box=self.box()
        box.fetcher=lambda url,limit:{"raw":b'<html><body><p>Quarterly earnings information and official financial statements are available for review on this page.</p><a href="https://assets-ir.tesla.com/q2.pdf">Shareholder download</a></body></html>',"status":200,"content_type":"text/html","final_url":url}
        result=box.profile("tesla_ir")
        self.assertEqual(result["status"],"usable")
        self.assertEqual(box.links(result["id"])["network_requests"],0)
        self.assertEqual(box.budget()["reserved_attempts"],1)

    def test_redirect_needs_a_separate_reservation(self):
        with patch("ForecastAgent.tools.intelligence_box.core.public_url"):
            self.assertIsNone(Redirects({"approved.example"}).redirect_request(None,None,302,"",{},"https://approved.example/file"))

    def test_table_cells_spans_and_unit_context_preserved(self):
        result=tables(b'<p>Amounts in millions, except EPS</p><table><tr><th rowspan="2">Metric</th><th colspan="2">2026</th></tr><tr><td>Revenue</td><td>100</td></tr></table>',max_rows=1)
        table=result["tables"][0]
        self.assertIn("millions",table["context_before"])
        self.assertEqual(table["rows"][0][0]["rowspan"],"2")
        self.assertEqual(table["rows"][0][1]["colspan"],"2")
        self.assertTrue(table["rows_truncated"])
        self.assertFalse(result["merged_cells_normalized"])

    def test_provisions_beyond_initial_excerpt_remain_readable(self):
        box=self.box()
        raw=('<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation" DocumentURI="http://www.legislation.gov.uk/ukpga/2025/18">'+''.join(f'<P1 id="section-{i}"><Text>Provision {i} applies.</Text></P1>' for i in range(1,502))+'</Legislation>').encode()
        box.fetcher=lambda url,limit:{"raw":raw,"status":200,"content_type":"application/xml","final_url":url}
        result=box.fetch("uk_legislation_xml",{"type":"ukpga","year":2025,"number":18})
        self.assertEqual(len(result["records"]),500)
        self.assertTrue(result["coverage"]["more_available"])
        lookup=box.provisions(result["id"],"section-501")
        self.assertEqual(lookup["status"],"found")
        self.assertIn("501",lookup["text"])
        self.assertEqual(box.budget()["reserved_attempts"],1)

    def test_document_byte_policy_cannot_silently_change(self):
        self.box()
        with self.assertRaisesRegex(ValueError,"byte cap cannot change"): Toolbox(self.temp.name,max_requests=3,max_document_bytes=16_000_000)

    def test_large_pdf_respects_task_cap_without_changing_native_default(self):
        from ForecastAgent.tools.intelligence_box.documents import read_document
        from ForecastAgent.readers.encoding import MAX_DECODED_BYTES
        from ForecastAgent.evidence.document import Document
        raw=b'%PDF-'+b' '*8_000_000
        source={"url":"https://assets-ir.tesla.com/report.pdf","final_url":"https://assets-ir.tesla.com/report.pdf","content_type":"application/pdf","captured_at":"2026-10-10T00:00:00+00:00","decoded_byte_cap":16_000_000}
        with patch("ForecastAgent.readers.pdf.parse_pdf",return_value=[Document("Issuer report financial statements",{"page":1})]):
            page=read_document(source,raw)
        self.assertEqual(page["decoded_bytes"],len(raw))
        self.assertEqual(MAX_DECODED_BYTES,8_000_000)
        source["decoded_byte_cap"]=8_000_000
        with self.assertRaisesRegex(ValueError,"size limit"): read_document(source,raw)


if __name__=="__main__": unittest.main()
