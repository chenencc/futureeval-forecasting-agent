"""Checkpoint-backed continuation never starts a duplicate active worker."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from ForecastAgent.market_repricing.common import now, write
from ForecastAgent.market_repricing.continuation import run


class Continuation(unittest.TestCase):
    def state(self, root):
        write(Path(root)/'state.json', {'tasks':{'test':{'status':'queued','executions':0}},
                                      'provider_reservations':[]})

    def test_active_worker_prevents_duplicate_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            self.state(root)
            with patch.dict(os.environ, {'GITHUB_RUN_ID':'10'}), patch(
                'ForecastAgent.market_repricing.continuation.api', return_value={
                    'workflow_runs':[{'id':11,'status':'queued'}]}), patch(
                'ForecastAgent.market_repricing.continuation.urlopen') as post:
                report=run(root,'123')
            self.assertEqual(report['status'],'another_worker_already_scheduled')
            post.assert_not_called()

    def test_verified_dispatch_is_acknowledged_with_same_monitor(self):
        with tempfile.TemporaryDirectory() as root:
            self.state(root)
            response=MagicMock(); response.__enter__.return_value.status=204
            with patch.dict(os.environ, {'GITHUB_RUN_ID':'10','GITHUB_REPOSITORY':'example/repo',
                'GH_STATE_TOKEN':'test-fixture'}), patch(
                'ForecastAgent.market_repricing.continuation.api', return_value={
                    'workflow_runs':[{'id':10,'status':'in_progress'}]}), patch(
                'ForecastAgent.market_repricing.continuation.urlopen',return_value=response) as post:
                report=run(root,'123')
            import json
            payload=json.loads(post.call_args.args[0].data)
            self.assertEqual(payload['inputs']['monitor_run_id'],'123')
            self.assertEqual(report['http_status'],204)
            self.assertFalse(report['budget_reset'])

    def test_verification_and_exhausted_day_make_no_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            self.state(root)
            with patch.dict(os.environ, {'GITHUB_RUN_ID':'10'}), patch(
                'ForecastAgent.market_repricing.continuation.api') as api:
                run(root,'123',verification_only=True)
                write(Path(root)/'state.json', {'tasks':{'test':{'status':'queued','executions':0}},
                    'provider_reservations':[{'kind':'model','day':now()[:10]}]*400})
                run(root,'123')
            api.assert_not_called()


if __name__=='__main__': unittest.main()
