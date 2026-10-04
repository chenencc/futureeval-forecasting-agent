"""Read-only web projection must preserve links while excluding secrets."""
import tempfile
import unittest
import json
from pathlib import Path
from ForecastAgent.web_export.export import clean,safe_link,task

class WebExportTests(unittest.TestCase):
    def test_url_and_secret_redaction(self):
        self.assertEqual(clean('https://example.org/path'), 'https://example.org/path')
        self.assertNotIn('sk-or-v1-',clean('key sk-or-v1-abcdefghijklmno'))
        self.assertEqual(clean('D:\\private\\data.json'),'[REDACTED]')
        self.assertEqual(safe_link('https://example.org/x?api_key=secret&date=2026-01-01'),'https://example.org/x?date=2026-01-01')
        self.assertIsNone(safe_link('javascript:alert(1)'))
        self.assertIsNone(safe_link('https://user:password@example.org'))
    def test_missing_receipt_and_unknown_usage_are_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);body={'request':{'id':'123','question':'Test?','resolution_criteria':'Rules'},
                'pages':{'https://example.org':{'content':'Body','headers':{'Authorization':'secret'}}},
                'model_attempts':[{'request':{'Authorization':'private'}}]}
            (p/'analysis-input.json').write_text(json.dumps(body),encoding='utf-8')
            out=task(p,'456');self.assertEqual(out['delivery']['status'],'not_recorded')
            self.assertIsNone(out['usage']['tokens'])
            self.assertNotIn('Authorization',json.dumps(out))
            self.assertEqual(out['sources'][0]['characters'],4)

if __name__=='__main__':unittest.main()
