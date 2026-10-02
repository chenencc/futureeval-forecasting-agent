"""Repair budgets, immutable lineage, crash reservations and overlay integrity."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from ForecastAgent.supplement.stage import run, analysis_overlay, digest


class SupplementTests(unittest.TestCase):
    def fixture(self,folder):
        raw=b'<html><body>Please enable javascript</body></html>'
        url='https://example.org/data'
        page={'url':url,'final_url':url,'content_type':'text/html','sha256':hashlib.sha256(raw).hexdigest(),
            'raw_response_base64':base64.b64encode(raw).decode(),'content':'Please enable javascript',
            'body_diagnostics':{'state':'javascript_shell','usable_text':False}}
        bundle={'pages':{},'fetch_attempts':[{'url':url,'status':'failed','detail':'Empty page or access interstitial'}],
            'failed_captures':[{'url':url,'page':page}]}
        campaign={'tasks':{'1':{'status':'closed_with_gaps','resources':{'tavily_basic':3,'exa':1}}}}
        archive=folder/'parent.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('campaign.json',json.dumps(campaign));z.writestr('tasks/1/bundle.json',json.dumps(bundle))
        return archive,bundle

    def test_resume_does_not_repeat_browser_and_parent_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,bundle=self.fixture(folder);before=archive.read_bytes()
            page={'content':'Measured data. '*40,'body_diagnostics':{'state':'readable','usable_text':True}}
            with patch('ForecastAgent.supplement.stage.render_page',return_value=page) as browser:
                run(archive,folder/'out',['1'],network=True)
                run(archive,folder/'out',['1'],network=True)
                self.assertEqual(browser.call_count,1)
            self.assertEqual(before,archive.read_bytes())
            overlay=analysis_overlay(bundle,folder/'out','1')
            self.assertNotEqual(overlay['pages'],bundle['pages'])
            self.assertEqual(bundle['pages'],{})
            with self.assertRaises(ValueError):analysis_overlay({'pages':{}},folder/'out','1')

    def test_failed_attempt_counts_and_policy_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,_=self.fixture(folder)
            with patch('ForecastAgent.supplement.stage.render_page',side_effect=RuntimeError('failed')) as browser:
                run(archive,folder/'out',['1'],network=True)
                run(archive,folder/'out',['1'],network=True)
                self.assertEqual(browser.call_count,1)
            with self.assertRaises(ValueError):run(archive,folder/'out',['1'],network=True,browser_limit=3)

    def test_offline_mode_never_renders(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,_=self.fixture(folder)
            with patch('ForecastAgent.supplement.stage.render_page') as browser:
                run(archive,folder/'out',['1'])
                browser.assert_not_called()

    def test_reserved_interruption_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,_=self.fixture(folder)
            run(archive,folder/'out',['1'],network=True,browser_limit=0)
            # Simulate a persisted reservation from a terminated worker.
            path=folder/'out/tasks/1/supplement.json';child=json.loads(path.read_text())
            child['attempts']=[{'url':'https://example.org/data','method':'browser','status':'reserved'}]
            path.write_text(json.dumps(child))
            with patch('ForecastAgent.supplement.stage.render_page') as browser:
                run(archive,folder/'out',['1'],network=True,browser_limit=0)
                browser.assert_not_called()


if __name__=='__main__':unittest.main()
