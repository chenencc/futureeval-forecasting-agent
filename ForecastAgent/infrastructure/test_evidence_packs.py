"""Lossless, corrupted-object, traversal and cache-reuse transport gates."""
import copy
from pathlib import Path
import tempfile
from unittest import TestCase
from . import evidence_packs as packs


class PackTests(TestCase):
    def build(self,root):
        data=b'{"source_clock":"2026-10-14T03:00:00Z","raw":"Original public body"}\n'
        return packs.build([{'key':'public:1','files':{'task/reading.json':data,'task/analysis.json':data}}],root,{'fixture':True})

    def test_two_original_paths_one_exact_object_and_lossless_restore(self):
        with tempfile.TemporaryDirectory() as root:
            manifest=self.build(root);case=manifest['cases'][0];data=(Path(root)/'packed'/case['pack']['path']).read_bytes()
            self.assertEqual(len(case['pack']['objects']),1)
            packs.store(Path(root)/'cache',case,data)
            self.assertTrue(packs.available(Path(root)/'cache',case))
            for row in case['files']:
                original=(Path(root)/'cache/restored'/row['path']).read_bytes()
                self.assertEqual(packs.sha(original),row['sha256'])

    def test_compressed_pack_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            case=self.build(root)['cases'][0];data=(Path(root)/'packed'/case['pack']['path']).read_bytes()
            with self.assertRaises(ValueError):packs.verify_pack(data[:-1]+b'x',case)

    def test_unexpected_object_allowance_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            case=self.build(root)['cases'][0];data=(Path(root)/'packed'/case['pack']['path']).read_bytes()
            case['pack']['objects']['0'*64]=100
            with self.assertRaises(ValueError):packs.verify_pack(data,case)

    def test_corrupt_cached_original_is_not_reported_as_reusable(self):
        with tempfile.TemporaryDirectory() as root:
            case=self.build(root)['cases'][0];data=(Path(root)/'packed'/case['pack']['path']).read_bytes()
            packs.store(Path(root)/'cache',case,data)
            next((Path(root)/'cache/objects').iterdir()).write_bytes(b'changed')
            with self.assertRaises(ValueError):packs.available(Path(root)/'cache',case)

    def test_traversal_absolute_or_duplicate_paths_are_rejected(self):
        for path in ('../original','/outside','C:/outside','a\\b'):
            with tempfile.TemporaryDirectory() as root,self.assertRaises(ValueError):
                packs.build([{'key':'public:1','files':{path:b'original'}}],root,{})

    def test_original_set_identity_is_preserved_without_json_reserialization(self):
        with tempfile.TemporaryDirectory() as root:
            raw=b' { "uncertainty": [0.1, 0.2], "utc": "2026-10-14T03:00:00Z" }\r\n'
            m=packs.build([{'key':'public:1','files':{'exact.json':raw}}],root,{})
            c=m['cases'][0];objects=packs.verify_pack((Path(root)/'packed'/c['pack']['path']).read_bytes(),c)
            self.assertEqual(objects[c['files'][0]['sha256']],raw)
