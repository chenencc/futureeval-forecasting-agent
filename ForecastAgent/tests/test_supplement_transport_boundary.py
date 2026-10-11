"""Physical transport deadlines, single execution and response identity."""
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ForecastAgent.supplement.transport import capture, CaptureFailure
from ForecastAgent.runtime.filesystem import io_path


class TransportBoundaryTests(unittest.TestCase):
    def test_completed_capture_is_read_back_and_cannot_be_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / 'fixture.py'
            operation = root / 'operation'
            script.write_text(
                "import json,sys\nfrom pathlib import Path\n"
                "r=json.loads(Path(sys.argv[1]).read_text())\n"
                "Path(sys.argv[2]).write_text(json.dumps({'request':r,'ok':True,"
                "'page':{'content':'original fixture'}}))\n", encoding='utf-8')
            command = [sys.executable, str(script), str(operation / 'request.json'),
                       str(operation / 'result.json')]
            page = capture('http', 'https://example.org/data', operation,
                           timeout_seconds=10, _command=command)
            self.assertEqual(page['content'], 'original fixture')
            self.assertEqual(page['supplement_transport_boundary']['status'], 'completed')
            request = (operation / 'request.json').read_bytes()
            with self.assertRaises(CaptureFailure) as raised:
                capture('http', 'https://example.org/data', operation, _command=command)
            self.assertFalse(raised.exception.audit['retry_issued'])
            self.assertEqual((operation / 'request.json').read_bytes(), request)

    def test_stalled_operation_terminates_and_retains_deadline_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            operation = Path(tmp) / 'operation'
            with self.assertRaises(CaptureFailure) as raised:
                capture('http', 'https://example.org/data', operation,
                        timeout_seconds=.5,
                        _command=[sys.executable, '-c', 'import time;time.sleep(30)'])
            receipt = raised.exception.audit
            self.assertEqual(receipt['status'], 'timed_out')
            self.assertTrue(receipt['process_tree_termination'])
            self.assertTrue(receipt['remote_attempt_unknown'])
            self.assertLess(receipt['elapsed_seconds'], 12)
            self.assertTrue((operation / 'request.json').exists())
            self.assertTrue((operation / 'process-receipt.json').exists())

    def test_response_identity_mismatch_never_becomes_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            operation = Path(tmp) / 'operation'
            command = [sys.executable, '-c',
                "from pathlib import Path;import sys;Path(sys.argv[1]).write_text("
                "'{\"request\":{},\"ok\":true,\"page\":{}}')",
                str(operation / 'result.json')]
            with self.assertRaises(CaptureFailure) as raised:
                capture('http', 'https://example.org/data', operation,
                        timeout_seconds=10, _command=command)
            self.assertFalse(raised.exception.audit['response_identity_verified'])

    def test_path_adaptation_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapted = io_path(Path(tmp) / 'record.json')
            self.assertEqual(io_path(adapted), adapted)

    def test_worker_keeps_crawl4ai_profile_and_admission_gate(self):
        from ForecastAgent.supplement.transport import worker
        from ForecastAgent.supplement.stage import save
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            request={'method':'browser','url':'https://example.org/data',
                     'retrieved_at_utc':'2026-10-11T00:00:00Z',
                     'browser_profile':{'backend':'crawl4ai','query':'regional metric'}}
            save(root/'request.json',request)
            denied={'content':'denied fixture','documents':[],
                    'capture_status':{'usable_text':False}}
            with patch('ForecastAgent.acquisition.nextgen.render_page',return_value=denied) as render:
                worker(root/'request.json',root/'result.json')
                self.assertEqual(render.call_args.kwargs['query'],'regional metric')
            result=json.loads((root/'result.json').read_text())
            self.assertEqual(result['request'],request)
            self.assertEqual(result['page']['content'],'')
            self.assertEqual(result['page']['excluded_body']['content'],'denied fixture')
