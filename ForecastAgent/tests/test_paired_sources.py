"""Reservation, immutable-parent and shared-response tests for paired trials."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.acquisition.paired_sources import run
from ForecastAgent.readers.loader import load_response


class PairedTrialTests(unittest.TestCase):
    def test_partial_render_cannot_replace_complete_body(self):
        from ForecastAgent.acquisition.paired_sources import prefer_capture
        complete = {'content': 'Complete dated source body', 'body_diagnostics': {'usable_text': True}}
        partial = {'content': 'Initial browser title', 'capture_status': {'usable_text': True, 'render_complete': False}}
        self.assertIs(prefer_capture(complete, partial), complete)
        self.assertIs(prefer_capture(None, partial), partial)
        denied = {'content': 'HTTP denial', 'capture_status': {'usable_text': False}}
        self.assertIs(prefer_capture(complete, denied), complete)

    def test_resume_preserves_failures_and_checks_capture_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root/'parent.json'
            parent.write_text(json.dumps({'pages': {}}), encoding='utf-8')
            manifest = {'limits': {'common_http_per_question': 2, 'browser_per_arm_per_question': 2,
                'browser_request_limit': 25, 'browser_timeout_ms': 20000,
                'local_html_reparses_per_arm_per_question': 8}, 'cases': [{
                    'request': {'id': '123', 'question': 'Will a board set its rate?',
                        'resolution_criteria': 'A formal board decision is required.'},
                    'parent_file': str(parent), 'parent_sha256': hashlib.sha256(parent.read_bytes()).hexdigest(),
                    'sources': [{'id': 'rate', 'url': 'https://example.org/rate', 'browser': True}],
                    'checks': [{'id': 'rate', 'label': 'Rate present', 'all_patterns': ['4.5%']}]}]}
            page = load_response({'url': 'https://example.org/rate', 'final_url': 'https://example.org/rate',
                'raw': b'<html><body><article>Official board notice: the policy rate is 4.5%.</article></body></html>',
                'content_type': 'text/html', 'charset': 'utf-8', 'response_headers': {}}, retrieved_at='2026-10-08T00:00:00Z')
            counts = {'http': 0, 'release': 0, 'candidate': 0}
            def fetch(_):
                counts['http'] += 1
                return page
            def release(*args, **kwargs):
                counts['release'] += 1
                raise TimeoutError('Preserve this failed reservation')
            def candidate(*args, **kwargs):
                counts['candidate'] += 1
                return page
            with patch('ForecastAgent.acquisition.paired_sources.require_backend'), patch(
                    'ForecastAgent.acquisition.paired_sources.enrich_html', return_value=page):
                result = run(manifest, root/'out', _fetch=fetch, _release=release, _candidate=candidate)
                self.assertEqual(counts, {'http': 1, 'release': 1, 'candidate': 1})
                self.assertEqual(len(result['attempts']), 4)
                run(manifest, root/'out', _fetch=fetch, _release=release, _candidate=candidate)
                self.assertEqual(counts, {'http': 1, 'release': 1, 'candidate': 1})
                failed = [a for a in result['attempts'] if a['status'] == 'failed']
                self.assertEqual(len(failed), 1)
                capture = next(a for a in result['attempts'] if a.get('capture_file'))
                (root/'out'/capture['capture_file']).write_text('{}')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    run(manifest, root/'out', _fetch=fetch, _release=release, _candidate=candidate)

    def test_changed_parent_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'parent.json'
            path.write_text('{}')
            from ForecastAgent.acquisition.paired_sources import validate
            manifest = {'limits': {'common_http_per_question': 2, 'browser_per_arm_per_question': 2,
                'browser_request_limit': 25, 'browser_timeout_ms': 20000,
                'local_html_reparses_per_arm_per_question': 8}, 'cases': [{
                    'request': {'id': '123', 'question': 'Will a board set its rate?',
                        'resolution_criteria': 'A formal board decision is required.'},
                    'parent_file': str(path), 'parent_sha256': '0'*64,
                    'sources': [{'id': 'rate', 'url': 'https://example.org/rate', 'browser': False}], 'checks': []}]}
            with self.assertRaisesRegex(ValueError, 'parent changed'):
                validate(manifest)


if __name__ == '__main__':
    unittest.main()
