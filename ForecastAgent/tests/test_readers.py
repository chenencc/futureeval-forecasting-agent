import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ForecastAgent.readers.loader import load_response
from ForecastAgent.runtime.budget import reserve


def load(raw, kind, maximum=150_000):
    return load_response({"url": "https://example.org/source", "final_url": "https://example.org/source",
                          "content_type": kind, "charset": "utf-8", "raw": raw},
                         retrieved_at="2026-09-30T00:00:00Z", max_chars=maximum)


class ReaderTests(TestCase):
    def test_csv_keeps_row_provenance_and_original_bytes(self):
        raw = b'date,value\n2026-08-19,5.19\n2026-08-20,5.20\n'
        result = load(raw, "text/csv")
        self.assertEqual(result["documents"][1]["metadata"]["row"], 2)
        self.assertEqual(json.loads(result["documents"][0]["page_content"])["value"], "5.19")
        self.assertEqual(base64.b64decode(result["raw_response_base64"]), raw)

    def test_invalid_json_is_explicit_and_parsing_needs_no_network(self):
        with self.assertRaises(json.JSONDecodeError):
            load(b'not JSON', "application/json")
        result = load(b'{"value":5.19}', "application/json")
        self.assertEqual(result["documents"][0]["metadata"]["format"], "json")

    def test_document_limit_does_not_truncate_original_snapshot(self):
        raw = b'<p>' + b'a'*100 + b'</p>'
        result = load(raw, "text/html", 30)
        self.assertEqual(len(result["documents"][0]["page_content"]), 30)
        self.assertTrue(result["documents_truncated"])
        self.assertTrue(result["content_truncated"])
        self.assertEqual(base64.b64decode(result["raw_response_base64"]), raw)

    def test_reserved_unknown_outcomes_survive_restore(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            bundle = {"searches": []}
            def save():
                path.write_text(json.dumps(bundle))
            for _ in range(3):
                reserve(bundle, "searches", {"status": "reserved"}, 3, save)
            restored = json.loads(path.read_text())
            with self.assertRaises(ValueError):
                reserve(restored, "searches", {"status": "reserved"}, 3, save)
