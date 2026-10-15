"""Actual selected-engine activation and native worker recovery, zero providers."""
import os
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.engineering.common import connect
from ForecastAgent.engineering.native import market_worker
from ForecastAgent.engineering.trace import summary
from ForecastAgent.market_repricing.runtime import activate


class SelectedEngine(unittest.TestCase):
    def test_engine_activation_retains_engineering_and_native_attempts(self):
        from ForecastAgent.engineering import common as before
        activate(os.environ['MARKET_ENGINE_DIRECTORY'])
        from ForecastAgent.engineering import common as after
        self.assertIs(before, after)
        from ForecastAgent.market_repricing.common import now, policy, digest, write
        from ForecastAgent.market_repricing.venues import normalize
        from ForecastAgent.market_repricing.tests.test_repricing import raw_poly
        from ForecastAgent.market_repricing.worker import request_for
        from ForecastAgent.market_repricing import worker
        market = normalize('polymarket', raw_poly() | {'createdAt': now()})
        native = {'tasks': {market['key']: {'market': market, 'executions': 0, 'status': 'queued'}}}
        original = worker.execute
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = connect(root / 'engineering')
            write(root / 'native/tasks' / digest(market['key']) / 'anchor.json',
                  {'status': 'usable', 'received_at': now(), 'midpoint': .45})
            try:
                with market_worker(db, 'offline-selected-engine'):
                    worker.execute(root / 'native', native, market['key'], policy(), None,
                                   collector=lambda *args: {'request': request_for(market), 'pages': {}},
                                   forecaster=lambda *args: self.fail('An empty body cannot invoke a model'))
                self.assertIs(worker.execute, original)
                self.assertEqual(native['tasks'][market['key']]['status'], 'baseline_only')
                self.assertEqual(native['tasks'][market['key']]['executions'], 1)
                self.assertFalse(native.get('provider_reservations'))
                report = summary(db)
                self.assertEqual(report['stages']['task_execution']['completed'], 1)
                self.assertEqual(report['stages']['acquisition_and_mapping']['completed'], 1)
                self.assertNotIn('analysis', report['stages'])
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
