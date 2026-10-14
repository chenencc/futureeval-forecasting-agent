"""Promotion receipts preserve originals and reject incompatible pending work."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ForecastAgent.market_repricing.common import read,write,seal
from ForecastAgent.market_repricing.tests.verify_acquisition_promotion import check


class Promotion(unittest.TestCase):
    def fixture(self,root,status='completed'):
        value={'tasks':{'old':{'status':status,'pipeline_commit':'b'*40,'executions':2}},
               'daily':{'2026-10-14':{'model':17}},'provider_reservations':[{'kind':'model','http_status':200}]}
        write(root/'state.json',value);write(root/'original.json',{'source':'saved body','capture_at':'2026-10-14T00:00:00Z'})
        seal(root)
        return value

    def test_terminal_state_and_budget_remain_byte_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'worker';old=self.fixture(root)
            before={p.name:p.read_bytes() for p in root.iterdir()}
            with patch('ForecastAgent.market_repricing.tests.verify_acquisition_promotion.verify_checkout',return_value={'pipeline_commit':'a'*40}):
                check(root,Path(temporary)/'report.json')
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})
            self.assertEqual(read(root/'state.json'),old)
            self.assertEqual(read(Path(temporary)/'report.json')['provider_calls'],0)

    def test_started_old_pipeline_blocks_switch_without_rewriting_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'worker';self.fixture(root,status='running');before=(root/'state.json').read_bytes()
            with patch('ForecastAgent.market_repricing.tests.verify_acquisition_promotion.verify_checkout',return_value={'pipeline_commit':'a'*40}):
                with self.assertRaises(ValueError):check(root,Path(temporary)/'report.json')
            self.assertEqual((root/'state.json').read_bytes(),before)

    def test_changed_original_fails_before_deployment_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'worker';self.fixture(root);write(root/'original.json',{'source':'changed'})
            with patch('ForecastAgent.market_repricing.tests.verify_acquisition_promotion.verify_checkout') as deploy:
                with self.assertRaises(ValueError):check(root,Path(temporary)/'report.json')
            deploy.assert_not_called()
