"""Actual shell classification, saved hash checks and durable routing gates."""
import base64
import hashlib
import unittest
from datetime import datetime,timezone
from ForecastAgent.readers.capture_status import capture_status,repair_route


class CaptureStatusTests(unittest.TestCase):
    def test_stale_readable_flag_does_not_hide_navigation_or_login(self):
        for body,state in [('Log in\nPassword\nForgot password?\nCreate new account','login_shell'),
                           ('Valmynd\nLeita\nHoppa yfir valmynd\nHeim\nFréttir\nÍþróttir','navigation_shell')]:
            row=capture_status({}, {'content':body,'body_diagnostics':{'state':'readable','usable_text':True}})
            self.assertEqual(row['body_state'],state);self.assertFalse(row['usable_text'])

    def test_short_real_notice_is_preserved(self):
        self.assertTrue(capture_status({}, {'content':'Official notice: the report was released on June 12, 2026.'})['usable_text'])

    def test_raw_hash_mismatch_never_routes_to_network(self):
        row=capture_status({}, {'raw_response_base64':'YQ==','sha256':'bad','content':'An apparently readable announcement that must not hide corrupt bytes.'})
        self.assertEqual(row['category'],'raw_integrity_gap')
        self.assertFalse(row['usable_text'])
        self.assertIsNone(repair_route(row,[],'https://example.org',network=True)['method'])

    def test_verified_parse_failure_routes_locally(self):
        raw=b'<html><body>Saved content</body></html>'
        row=capture_status({}, {'content':'','parse_failure':{},'raw_response_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()})
        self.assertEqual(repair_route(row,[],'https://example.org')['method'],'reparse')

    def test_retry_deadline_reservation_and_historical_gates(self):
        now=datetime(2026,10,3,tzinfo=timezone.utc)
        row=capture_status({'detail':'HTTP Error 503'}, {'content':'','retrieved_at_utc':now.isoformat(),'response_headers':{'Retry-After':'120'}},now=now)
        self.assertEqual(repair_route(row,[],'https://example.org',network=True,now=now)['reason'],'retry_deadline_pending')
        row.pop('retry_at_utc')
        self.assertIsNone(repair_route(row,[{'url':'https://example.org','status':'reserved'}],'https://example.org',network=True)['method'])
        self.assertIsNone(repair_route(row,[],'https://example.org',network=True,historical=True)['method'])

    def test_successful_http_login_shell_is_not_a_failed_physical_request(self):
        import json,tempfile,zipfile
        from pathlib import Path
        from unittest.mock import patch
        from ForecastAgent.runtime.gap_repair import inventory
        from ForecastAgent.supplement.stage import run
        url='https://example.org/login'
        bundle={'request':{'id':'1','mode':'live'},'pages':{url:{'content':'Log in\nPassword\nForgot password?\nCreate new account'}},
                'fetch_attempts':[{'url':url,'status':'completed'}]}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive=root/'parent.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('campaign.json',json.dumps({'tasks':{'1':{'status':'acquired'}}}))
                z.writestr('tasks/1/bundle.json',json.dumps(bundle))
            plan=inventory(archive)
            self.assertEqual(plan['failed_physical_fetch_attempts'],0)
            self.assertEqual(plan['body_quality_gap_count'],1)
            with patch('ForecastAgent.supplement.stage.fetch_document') as http,patch('ForecastAgent.supplement.stage.render_page') as browser:
                run(archive,root/'out',['1'],network=True)
                http.assert_not_called();browser.assert_not_called()

    def test_reserved_source_fetch_is_not_repeated(self):
        import copy,tempfile
        from unittest.mock import patch
        from ForecastAgent.tests.test_runtime_contracts import prepared,URL,LIVE
        with tempfile.TemporaryDirectory() as d:
            task=prepared(d,copy.deepcopy(LIVE));task.bundle['pages'].pop(URL,None)
            task.bundle['fetch_attempts'].append({'url':URL,'status':'reserved'})
            with patch('ForecastAgent.runtime.retrieval.fetch_public_page') as fetch:
                with self.assertRaisesRegex(ValueError,'already attempted|Prior failed or reserved'):
                    task.execute('fetch_page',{'url':URL},'')
                fetch.assert_not_called()


if __name__=='__main__':unittest.main()
