"""Preservation and interrupted-request accounting for targeted URL repair."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import save, load
from ForecastAgent.supplement import targeted


class TargetedTests(unittest.TestCase):
    def test_failed_shells_are_preserved_and_never_retried_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); parent = root/'parent'; output = root/'out'
            task = parent/'recollection-fifty-free-repair-0/tasks/1'
            save(task/'analysis-input.json', {'request': {'id': '1'}, 'pages': {}})
            save(task/'repair/tasks/1/supplement.json', {'attempts': []})
            plan = root/'plan.json'
            save(plan, {'tasks': {'1': {'sources': [{'url': f'https://example.org/{i}'} for i in range(9)]}}})
            shell = {'content': 'Navigation only', 'raw_response_base64': 'c2hlbGw=',
                     'body_diagnostics': {'usable_text': False}}
            with patch.object(targeted, 'fetch_document', return_value=shell) as http, patch.object(targeted, 'render_page', return_value=shell) as browser:
                targeted.run(parent, root/'baseline', output, plan, True)
                targeted.run(parent, root/'baseline', output, plan, True)
                self.assertEqual(http.call_count, 6)
                self.assertEqual(browser.call_count, 2)
            state = load(output/'tasks/1/repair-state.json')
            self.assertEqual(len(state['attempts']), 8)
            self.assertEqual(state['captures'], [])
            self.assertTrue(all((output/'tasks/1'/r['response_file']).exists() for r in state['attempts']))

    def test_resume_preserves_attempts_and_parent_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); parent = root/'parent'; output = root/'out'
            task = parent/'recollection-fifty-free-repair-0/tasks/1'
            bundle = {'request': {'id': '1'}, 'pages': {}, 'searches': [{'reserved': True}]}
            save(task/'analysis-input.json', bundle)
            prior = {'attempts': [{'status': 'failed', 'tool': 'http'}]}
            save(task/'repair/tasks/1/supplement.json', prior)
            plan = root/'plan.json'
            save(plan, {'tasks': {'1': {'sources': [{'url': 'https://example.org/detail'}]}}})
            page = {'content': 'Original authoritative body', 'body_diagnostics': {'usable_text': True}}
            with patch.object(targeted, 'fetch_document', return_value=page) as fetch:
                targeted.run(parent, root/'baseline', output, plan, True)
                targeted.run(parent, root/'baseline', output, plan, True)
                self.assertEqual(fetch.call_count, 1)
            self.assertEqual(load(output/'tasks/1/prior-supplement.json'), prior)
            self.assertEqual(load(output/'tasks/1/analysis-input.json')['searches'], bundle['searches'])
            changed = load(plan); changed['tasks']['1']['sources'].append({'url': 'https://example.org/other'})
            save(plan, changed)
            with self.assertRaisesRegex(ValueError, 'Frozen'):
                targeted.run(parent, root/'baseline', output, plan, True)


if __name__ == '__main__':
    unittest.main()
