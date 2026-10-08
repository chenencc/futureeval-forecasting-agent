"""Offline checks for paced, resumable one-off platform delivery."""
from email.message import Message
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from ForecastAgent.market_pulse.manual_delivery import PacedClient


class PacedTransportTests(unittest.TestCase):
    def test_get_429_preserves_backoff_and_retries_read_only(self):
        headers = Message(); headers['Retry-After'] = '12'
        failure = HTTPError('https://www.metaculus.com/api/posts/1/', 429,
                            'Too Many Requests', headers, None)
        with tempfile.TemporaryDirectory() as folder:
            client = PacedClient('offline-test', folder)
            with patch.object(client, '_pace'), patch.object(client, '_send',
                    side_effect=[failure, {'status': 200, 'data': {}}]) as send:
                result = client.request('GET', 'posts/1/')
            self.assertEqual(result['status'], 200)
            self.assertEqual(send.call_count, 2)
            records = list(Path(folder).glob('*.json'))
            self.assertEqual(len(records), 1)
            import json
            record = json.loads(records[0].read_text())
            self.assertEqual(record['retry_after_seconds'], 12)
            self.assertFalse(record['post_retried'])
            self.assertGreater(client.next_request_at, 0)

    def test_post_is_never_retried_and_invalidates_read_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            client = PacedClient('offline-test', folder)
            client.post_cache['1'] = (0, {})
            failure = HTTPError('https://www.metaculus.com/api/questions/bulk-forecast-comment/',
                                429, 'Too Many Requests', None, None)
            with patch.object(client, '_pace'), patch.object(client, '_send',
                    side_effect=failure) as send:
                with self.assertRaises(HTTPError):
                    client.request('POST', 'questions/bulk-forecast-comment/', {})
            self.assertEqual(send.call_count, 1)
            self.assertEqual(client.post_cache, {})
            self.assertEqual(list(Path(folder).glob('*.json')), [])

    def test_adjacent_parent_read_is_copied_and_post_requires_fresh_read(self):
        with tempfile.TemporaryDirectory() as folder:
            client = PacedClient('offline-test', folder)
            replies = [{'status': 200, 'data': {'id': 1, 'generation': 1}},
                       {'status': 201, 'data': {}},
                       {'status': 200, 'data': {'id': 1, 'generation': 2}}]
            with patch.object(client, '_pace'), patch.object(client, '_send',
                    side_effect=replies) as send:
                first = client.post(1); first['generation'] = -1
                self.assertEqual(client.post(1)['generation'], 1)
                self.assertEqual(send.call_count, 1)
                client.request('POST', 'questions/bulk-forecast-comment/', {})
                self.assertEqual(client.post(1)['generation'], 2)
                self.assertEqual(send.call_count, 3)

    def test_unknown_endpoint_keeps_original_endpoint_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            client = PacedClient('offline-test', folder)
            with patch.object(client, '_pace'), self.assertRaises(ValueError):
                client.request('GET', 'https://example.com/secret')


if __name__ == '__main__':
    unittest.main()
