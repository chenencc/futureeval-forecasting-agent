"""Transport preservation, atomic failure and projection compatibility gates."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ForecastAgent.market_repricing.common import seal, verify, write
from ForecastAgent.infrastructure import market_checkpoint as transport


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'original'
        self.key = 'a' * 64
        write(self.root / 'state.json', {'tasks': {'test': {'status': 'queued', 'executions': 2}},
            'provider_reservations': [{'status': 'failed', 'kind': 'model'}]})
        self.folder = self.root / 'tasks' / self.key
        write(self.folder / 'collection-request.json', {'frozen': True})
        write(self.folder / 'evidence-capture-v2/identity.json', {'frozen_capture': True})
        raw = b'{ "source": "original", "clock": "2026-10-14", "padding": "' + b'x' * 20000 + b'" }\n'
        for name in ['analysis-input.json', 'reading-input.json']:
            path = self.folder / name
            path.write_bytes(raw)
        seal(self.root)
        self.bytes = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def zipped(self, source, name='source.zip'):
        path = self.base / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in source.rglob('*'):
                if file.is_file():
                    archive.write(file, file.relative_to(source).as_posix())
        return path

    def assert_originals(self, root):
        for name, raw in self.bytes.items():
            self.assertEqual((root / name).read_bytes(), raw)
        verify(root)

    def test_lossless_deduplicated_roundtrip_and_stable_packs(self):
        first, second = self.base / 'first', self.base / 'second'
        receipt = transport.publish(self.root, first)
        transport.publish(self.root, second)
        self.assertLess(receipt['unique_task_object_bytes'], sum(len(v) for k, v in self.bytes.items() if k.startswith('tasks/')))
        self.assertEqual((first / 'packs' / (self.key + '.tar.xz')).read_bytes(),
                         (second / 'packs' / (self.key + '.tar.xz')).read_bytes())
        restored = self.base / 'restored'
        transport.extract_file(self.zipped(first), restored)
        self.assert_originals(restored)
        # Independent mutable paths, not hardlinks into other evidence.
        (restored / 'tasks' / self.key / 'analysis-input.json').write_bytes(b'changed')
        self.assertEqual((restored / 'tasks' / self.key / 'reading-input.json').read_bytes(),
                         self.bytes['tasks/' + self.key + '/reading-input.json'])

    def test_legacy_large_state_uses_new_finite_bound(self):
        from ForecastAgent.market_repricing import github_state
        raw = self.zipped(self.root)
        with patch.object(github_state, 'MAX_EXPANDED_BYTES', 100):
            with self.assertRaisesRegex(ValueError, 'Expanded state'):
                github_state.extract(raw.read_bytes(), self.base / 'old')
            transport.extract_file(raw, self.base / 'new')
        self.assert_originals(self.base / 'new')

    def test_projection_retains_ledger_and_compatibility_not_bodies(self):
        packed = self.base / 'packed'
        transport.publish(self.root, packed)
        for i, source in enumerate([self.root, packed]):
            dest = self.base / ('projection' + str(i))
            transport.extract_file(self.zipped(source, str(i) + '.zip'), dest, True)
            self.assertEqual((dest / 'state.json').read_bytes(), self.bytes['state.json'])
            self.assertEqual((dest / 'tasks' / self.key / 'collection-request.json').read_bytes(),
                             self.bytes['tasks/' + self.key + '/collection-request.json'])
            self.assertTrue((dest / 'tasks' / self.key / 'evidence-capture-v2/identity.json').is_file())
            self.assertFalse((dest / 'tasks' / self.key / 'analysis-input.json').exists())
            self.assertFalse(json.loads((dest / 'projection-receipt.json').read_text())['full_body_hash_verification'])
            verify(dest)

    def test_corrupt_pack_fails_atomically(self):
        packed = self.base / 'packed'
        transport.publish(self.root, packed)
        pack = next((packed / 'packs').iterdir())
        data = pack.read_bytes()
        pack.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        with self.assertRaises(ValueError):
            transport.extract_file(self.zipped(packed), self.base / 'bad')
        self.assertFalse((self.base / 'bad/state.json').exists())

    def test_expansion_and_path_guards(self):
        raw = self.zipped(self.root)
        with patch.object(transport, 'MAX_EXPANDED', 100):
            with self.assertRaisesRegex(ValueError, 'bound'):
                transport.extract_file(raw, self.base / 'bad')
        for name in ['../state.json', '/absolute', 'C:/windows', 'folder\\file']:
            with self.assertRaises(ValueError):
                transport.safe(name)

    def test_omitted_known_mutex_recovered_exactly(self):
        lock = self.folder / 'development-intelligence/a/.lock.recovery'
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b'0')
        seal(self.root)
        path = self.base / 'omitted.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for file in self.root.rglob('*'):
                if file.is_file() and file != lock:
                    archive.write(file, file.relative_to(self.root).as_posix())
        transport.extract_file(path, self.base / 'recovered')
        self.assertEqual((self.base / 'recovered' / lock.relative_to(self.root)).read_bytes(), b'0')

    def test_missing_original_and_duplicate_transport_fail_closed(self):
        (self.folder / 'analysis-input.json').unlink()
        with self.assertRaises(ValueError):
            transport.extract_file(self.zipped(self.root), self.base / 'missing')
        archive_path = self.base / 'duplicate.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('state.json', '{}')
            archive.writestr('state.json', '{}')
        with zipfile.ZipFile(archive_path) as archive:
            with self.assertRaises(ValueError):
                transport.zip_members(archive)


if __name__ == '__main__':
    unittest.main()
