"""Service failure recovery without duplicate or partial atomic submissions."""
import io, tempfile, unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from ForecastAgent.competition import platform
from ForecastAgent.competition.queue import load, utc
from ForecastAgent.tests.test_official_competition import FakePlatform

class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.client=FakePlatform()
        self.task={'id':'7','post_id':'42'};self.payload={'question':7,'probability_yes':.63}
        self.now=utc();self.original_clock=utc
        clock=lambda value=None:self.original_clock(value) if value else self.now
        self.patch=patch.object(platform,'utc',clock);self.patch.start();self.addCleanup(self.patch.stop)
    def deliver(self):return platform.deliver(self.client,self.task,self.payload,'Reasoning',self.folder,enabled=True)
    def service_error(self,after_accept=False):
        original=self.client.request
        def request(*args):
            if after_accept:original(*args)
            else:self.client.posts+=1
            raise HTTPError('https://www.metaculus.com/api/',503,'Unavailable',{},io.BytesIO(b'upstream unavailable'))
        self.client.request=request
        with self.assertRaisesRegex(RuntimeError,'503'):self.deliver()
        self.client.request=original
    def prepare_negative_reads(self):
        self.now+=timedelta(seconds=121)
        with self.assertRaisesRegex(RuntimeError,'duplicate POST'):self.deliver()
        self.now+=timedelta(seconds=31)
    def test_two_separated_reads_retry_same_payload_and_preserve_initial(self):
        self.service_error();initial=(self.folder/'submission.json').read_bytes()
        self.prepare_negative_reads()
        result=self.deliver()
        self.assertEqual(result['status'],'accepted');self.assertEqual(self.client.posts,2)
        self.assertEqual(len(result['post_attempts']),2)
        self.assertEqual(result['post_attempts'][0]['http_status'],503)
        self.assertEqual((self.folder/'submission-initial.json').read_bytes(),initial)
        self.deliver();self.assertEqual(self.client.posts,2)
    def test_success_behind_503_reconciles_without_repost(self):
        self.service_error(after_accept=True)
        self.assertEqual(self.deliver()['status'],'accepted');self.assertEqual(self.client.posts,1)
    def test_close_blocks_negative_readback_retry(self):
        self.service_error();self.prepare_negative_reads()
        self.client.document['question']['status']='closed'
        with self.assertRaisesRegex(RuntimeError,'deadline'):self.deliver()
        self.assertEqual(self.client.posts,1)
    def test_partial_forecast_without_marker_is_not_negative(self):
        self.service_error();self.prepare_negative_reads()
        self.client.document['question']['my_forecasts']={'latest':{'author_id':platform.BOT_ID,'forecast_values':[.37,.63]},'history':[]}
        with self.assertRaisesRegex(RuntimeError,'duplicate POST'):self.deliver()
        self.assertEqual(self.client.posts,1)
    def test_bounded_post_attempts_survive_restarts(self):
        self.service_error()
        for expected in (2,3):
            self.prepare_negative_reads()
            original=self.client.request
            def failed(*args):
                self.client.posts+=1
                raise HTTPError('https://www.metaculus.com/api/',503,'Unavailable',{},io.BytesIO(b'unavailable'))
            self.client.request=failed
            with self.assertRaisesRegex(RuntimeError,'503'):self.deliver()
            self.client.request=original
            self.assertEqual(self.client.posts,expected)
        self.now+=timedelta(minutes=10)
        for _ in range(3):
            with self.assertRaisesRegex(RuntimeError,'duplicate POST'):self.deliver()
            self.now+=timedelta(minutes=10)
        self.assertEqual(self.client.posts,3)
    def test_unreadable_own_history_fails_before_retry(self):
        self.service_error()
        self.client.document['question']['my_forecasts']={}
        with self.assertRaisesRegex(RuntimeError,'unreadable'):self.deliver()
        self.assertEqual(self.client.posts,1)

if __name__=='__main__':unittest.main()
