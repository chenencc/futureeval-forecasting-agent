"""Offline packet integrity and bounded source routing, with no engine dependency."""
import base64
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.common import read, write, digest, now
from ForecastAgent.market_repricing.evidence_capture import candidates, packet, verify_packet, recover


def diagnostic(page, url):
    return {'state': 'readable', 'usable_text': bool(page.get('content'))}


class EvidenceCapture(unittest.TestCase):
    def test_http_designated_and_official_detail_retained_ahead_of_social(self):
        bundle = {'request': {'question': 'Government diesel export decision',
            'resolution_criteria': 'Use http://government.example.ru/decree/123'},
            'searches': [{'search_role': 'primary', 'results': [
                {'url': 'https://www.facebook.com/example/post/1', 'title': 'Diesel government decision'},
                {'url': 'http://government.example.ru/decree/123', 'title': 'Decree 123'},
                {'url': 'http://government.ru/docs/456', 'title': 'Government diesel export decree'},
                {'url': 'https://news.example.com/story', 'title': 'Diesel export policy decision'}]}]}
        leads = candidates(bundle)
        self.assertEqual(leads[0]['url'], 'http://government.example.ru/decree/123')
        self.assertIn('rules_designated', leads[0]['roles'])
        self.assertTrue(any(r['url'] == 'http://government.ru/docs/456' for r in leads))
        self.assertFalse(any(r['relevance_verified'] for r in leads))

    def test_credentialed_and_non_http_targets_not_admitted(self):
        b = {'searches': [{'results': [{'url': u} for u in (
            'file:///tmp/document', 'https://user:password@example.org/a', 'https://polymarket.com/event/a')]}]}
        self.assertEqual(candidates(b), [])

    def test_first_packet_immutable_and_supplement_preserves_versions(self):
        raw = b'Original public text'
        b = {'request': {'id': '1', 'question': 'Example', 'resolution_criteria': 'Rule'},
            'pages': {'https://example.org/a': {'url': 'https://example.org/a', 'content': raw.decode(),
                'retrieved_at_utc': now(), 'raw_response_base64': base64.b64encode(raw).decode(),
                'sha256': hashlib.sha256(raw).hexdigest()}}, 'searches': [{'query': 'Example'}]}
        with tempfile.TemporaryDirectory() as root, patch('ForecastAgent.market_repricing.evidence_capture.quality', diagnostic):
            first = packet(b, root, 'initial')
            before = (Path(root) / 'first-packet.json').read_bytes()
            self.assertEqual(packet(b, root, 'same'), first)
            self.assertGreater(verify_packet(Path(root) / 'first-packet.json')['objects_verified'], 0)
            child = copy.deepcopy(b); raw2 = b'A later public version'
            child['pages']['https://example.org/a'].update(content=raw2.decode(),
                raw_response_base64=base64.b64encode(raw2).decode(), sha256=hashlib.sha256(raw2).hexdigest())
            second = packet(child, root, 'later')
            self.assertFalse(second['first_packet'])
            self.assertEqual(second['raw_changed_page_urls'], ['https://example.org/a'])
            self.assertEqual((Path(root) / 'first-packet.json').read_bytes(), before)
            self.assertEqual(verify_packet(Path(root) / 'supplements/0001.json')['network_calls'], 0)
            obj = Path(root) / first['pages']['https://example.org/a']['path']
            obj.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError): verify_packet(Path(root) / 'first-packet.json')

    def test_role_checklist_does_not_stop_at_two_bodies(self):
        bundle = {'request': {'question': 'Example', 'resolution_criteria': 'Source http://government.example.org/detail'},
            'pages': {'https://news.org/a': {'content': 'Report A'}, 'https://news.org/b': {'content': 'Report B'}},
            'searches': [{'results': [{'url': 'http://government.example.org/detail'},
                {'url': 'https://news.org/c'}, {'url': 'https://news.org/d'}, {'url': 'https://news.org/e'}]}]}
        calls = []
        def capture(method, url, directory, **kwargs):
            calls.append((method, url)); return {'url': url, 'content': 'Original public report', 'retrieved_at_utc': now()}
        def prepare(child): return child, {'fixture': True}
        with tempfile.TemporaryDirectory() as root, patch('ForecastAgent.market_repricing.evidence_capture.quality', diagnostic), patch(
            'ForecastAgent.market_repricing.materials.prepare', prepare):
            child, report = recover(bundle, root, capture)
            count = len(calls)
            again, replay = recover(bundle, root, capture)
            self.assertEqual(child, again); self.assertEqual(report, replay)
            self.assertEqual(len(calls), count)
            self.assertEqual(count, 4)
            self.assertIn(('http', 'http://government.example.org/detail'), calls)
            self.assertEqual(report['new_searches'], 0)
            self.assertFalse(report['budget_reset'])


if __name__ == '__main__':
    unittest.main()
