"""Check failure routing without fetching pages or resetting provider budgets."""
import unittest
from ForecastAgent.runtime.gap_repair import classify


class RepairRoutingTests(unittest.TestCase):
    def test_access_denial_is_not_automatically_rendered(self):
        for status in (401,403):
            result=classify({'detail':f'HTTP Error {status}: Forbidden'})
            self.assertEqual(result['category'],'access_restricted')
            self.assertFalse(result['execution_authorized'])

    def test_javascript_requires_observed_shell(self):
        result=classify({}, {'body_diagnostics':{'state':'javascript_shell'}})
        self.assertEqual(result['proposed_route'],'bounded_browser_render')
        unknown=classify({'detail':'Empty page or access interstitial'})
        self.assertEqual(unknown['proposed_route'],'inspect_saved_response_then_route')

    def test_saved_format_failure_routes_to_local_reparse(self):
        result=classify({'detail':'Unsupported content type: application/vnd.ms-excel'},
            {'raw_response_base64':'AA=='})
        self.assertTrue(result['saved_raw_available'])
        self.assertEqual(result['category'],'format_or_parser_gap')

    def test_rate_limit_is_not_browser_escalation(self):
        result=classify({'detail':'HTTP Error 429: Too Many Requests'})
        self.assertEqual(result['proposed_route'],'respect_retry_deadline')

    def test_historical_failure_already_rescued_is_not_retried(self):
        result=classify({'detail':'HTTP Error 403: Forbidden'},
            {'content':'Saved rescue body','body_diagnostics':{'state':'readable','usable_text':True}})
        self.assertEqual(result['proposed_route'],'reuse_existing_capture')


if __name__=='__main__':
    unittest.main()
