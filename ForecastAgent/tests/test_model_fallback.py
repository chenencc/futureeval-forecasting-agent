"""Physical transport failover tests with no external requests."""
import json
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError
from ForecastAgent.providers.model import ModelRoute, ULTRA_MODEL, SUPER_MODEL
from ForecastAgent.providers.ultra import ask_ultra


def bad(code):
    return HTTPError('https://example.com',code,'unavailable',{},BytesIO(b'{"error":{"message":"service unavailable"}}'))


class ModelFallbackTests(TestCase):
    @patch('ForecastAgent.runtime.batch_health.transport_records')
    def test_successful_fallback_does_not_pause_campaign(self, records):
        from ForecastAgent.collection_campaign import unavailable
        records.return_value=[{'status':'http_error','http_status':503},
            {'status':'http_error','http_status':503},{'status':'received'}]
        self.assertFalse(unavailable('.',[]))
        records.return_value+= [{'status':'http_error','http_status':503}]*2
        self.assertTrue(unavailable('.',[]))

    @patch('ForecastAgent.providers.ultra.time.sleep')
    @patch('ForecastAgent.providers.ultra.urlopen')
    def test_two_failures_then_super_and_sticky_success(self, opener, sleep):
        success=b'{"choices":[{"message":{"content":"ok"}}]}'
        opener.side_effect=[bad(503),bad(503),BytesIO(success),BytesIO(success)]
        route=ModelRoute();records=[]
        def observer(stage,record,token=None):
            if stage=='complete':records.append(record.copy())
        self.assertEqual(ask_ultra([], 'test', tools=[],model_route=route,observer=observer)['content'],'ok')
        ask_ultra([], 'test', tools=[],model_route=route,observer=observer)
        self.assertEqual([r['request']['model'] for r in records],[ULTRA_MODEL,ULTRA_MODEL,SUPER_MODEL,SUPER_MODEL])
        self.assertTrue(records[2]['model_routing']['fallback_active'])
        self.assertFalse(records[2]['model_routing']['budget_reset'])

    @patch('ForecastAgent.providers.ultra.time.sleep')
    @patch('ForecastAgent.providers.ultra.urlopen')
    def test_shared_daily_quota_never_falls_back(self, opener, sleep):
        opener.side_effect=bad(429);route=ModelRoute()
        with self.assertRaises(RuntimeError):ask_ultra([], 'test',tools=[],model_route=route)
        self.assertEqual(opener.call_count,1);self.assertFalse(route.fallback)
        sleep.assert_not_called()

    def test_success_breaks_consecutive_failure_streak(self):
        route=ModelRoute();route.observe({'status':'http_error','http_status':503})
        route.observe({'status':'received'})
        route.observe({'status':'missing_choices'})
        self.assertFalse(route.fallback)
        route.observe({'status':'missing_choices'})
        self.assertTrue(route.fallback)
        self.assertEqual(ModelRoute().model(),ULTRA_MODEL)

    @patch('ForecastAgent.providers.ultra.time.sleep')
    @patch('ForecastAgent.providers.ultra.urlopen')
    def test_connection_failures_switch_with_same_attempt_cap(self, opener, sleep):
        opener.side_effect=[TimeoutError(),TimeoutError(),BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')]
        route=ModelRoute();ask_ultra([], 'test',tools=[],model_route=route)
        self.assertEqual(opener.call_count,3);self.assertTrue(route.fallback)

    @patch('ForecastAgent.providers.ultra.urlopen')
    def test_existing_reservation_budget_prevents_fallback_request(self, opener):
        route=ModelRoute();route.fallback=True
        def observer(stage,record,token=None):
            if stage=='reserve':raise RuntimeError('Lifetime model attempt budget exhausted')
        with self.assertRaises(RuntimeError):ask_ultra([], 'test',tools=[],model_route=route,observer=observer)
        opener.assert_not_called()
