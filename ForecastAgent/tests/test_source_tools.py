"""Follow-up provenance and reservation checks, without external requests."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ForecastAgent.acquisition.source_tools import follow_observed
from ForecastAgent.tests.test_material_structure import page


class FollowTests(unittest.TestCase):
    def setUp(self):
        self.parent = page('<main><iframe src="https://example.org/data"></iframe></main>')
        self.selection = [{'url': 'https://example.org/data', 'render': True}]

    def test_resumed_capture_never_spends_twice_and_binds_parent(self):
        fetch = Mock(return_value=page('{"data":[{"value":2}]}', 'application/json'))
        render = Mock(return_value=page('<p>Rendered report</p>'))
        with tempfile.TemporaryDirectory() as d:
            first = follow_observed(self.parent, self.selection, d, _fetch=fetch, _render=render)
            self.assertEqual(len(first['attempts']), 2)
            capture = json.loads((Path(d)/first['attempts'][0]['capture_file']).read_text(encoding='utf-8'))
            self.assertEqual(capture['snapshot']['observed_resource_lineage']['parent_source_sha256'], self.parent['sha256'])
            self.assertEqual(capture['reading']['structured_data']['row_count'], 1)
            follow_observed(self.parent, self.selection, d, _fetch=fetch, _render=render)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(render.call_count, 1)
            capture_path = Path(d)/first['attempts'][0]['capture_file']
            capture_path.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'archive changed'):
                follow_observed(self.parent, self.selection, d, _fetch=fetch, _render=render)

    def test_unobserved_or_changed_selection_is_rejected_before_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                follow_observed(self.parent, [{'url': 'https://example.org/invented'}], d)
            fetch = Mock(side_effect=TimeoutError('fixture failure'))
            follow_observed(self.parent, self.selection, d, _fetch=fetch, _render=fetch)
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                follow_observed(self.parent, [{'url': 'https://example.org/data'}], d, _fetch=fetch)
            # Failed reservations remain spent; resume cannot automatically retry.
            follow_observed(self.parent, self.selection, d, _fetch=fetch, _render=fetch)
            self.assertEqual(fetch.call_count, 2)

    def test_parse_gap_keeps_original_body(self):
        capture = page('plain source text', 'text/plain')
        with tempfile.TemporaryDirectory() as d:
            result = follow_observed(self.parent, [{'url': 'https://example.org/data'}], d,
                                     _fetch=lambda _: capture)
            saved = json.loads((Path(d)/result['attempts'][0]['capture_file']).read_text(encoding='utf-8'))
            self.assertEqual(saved['reading']['state'], 'projection_gap')
            self.assertEqual(saved['snapshot']['sha256'], capture['sha256'])


if __name__ == '__main__':
    unittest.main()
