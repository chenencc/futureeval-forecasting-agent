"""Real deep record IO and reservation preservation without network requests."""
import shutil
import tempfile
import unittest
from pathlib import Path
from ForecastAgent.runtime.filesystem import io_path
from ForecastAgent.runtime.records import Journal, load, save


class WindowsRecordPathTests(unittest.TestCase):
    def test_deep_capture_and_restart_preserve_physical_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            deep = Path(directory)
            for index in range(8):
                deep = deep / ('capture-layer-' + str(index) + '-' + 'x' * 32)
            self.assertGreater(len(str(deep)), 300)
            try:
                capture = deep / 'capture.json'
                record = {'source': 'public capture', 'text': 'València 2026', 'truth_verified': False}
                save(capture, record)
                self.assertEqual(load(capture), record)
                journal = deep / 'model-http'
                token = Journal(journal, 1)('reserve', {'status': 'reserved'})
                self.assertEqual(token.parent, io_path(journal))
                self.assertEqual(load(token)['status'], 'reserved')
                Journal(journal, 1)('received', {'status': 'received', 'usage': {'total_tokens': 7}}, token)
                with self.assertRaises(RuntimeError):
                    Journal(io_path(journal), 1)('reserve', {'status': 'reserved'})
                self.assertEqual(len(list(io_path(journal).glob('*.json'))), 1)
                self.assertEqual(load(token)['usage']['total_tokens'], 7)
            finally:
                shutil.rmtree(io_path(directory))


if __name__ == '__main__':
    unittest.main()
