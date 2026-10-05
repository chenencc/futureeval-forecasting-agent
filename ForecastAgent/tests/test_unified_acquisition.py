"""Integration gates use saved bodies and mock providers, spending no quota."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.acquisition import pipeline
from ForecastAgent.analysis.pilot import load, save, digest


class UnifiedAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'task'
        self.request = {'id': '101', 'pipeline': 'collection', 'mode': 'live', 'question': 'A public document'}
        self.body = 'The official publication describes the project, its participants and the observation period. ' * 40
        self.bundle = {'request': self.request, 'pages': {'https://example.org/a': {'content': self.body}},
            'result': {'gaps': [], 'incomplete': False}, 'searches': [{'status': 'completed'}],
            'model_attempts': [{'status': 'received'}]}

    def collect(self, *, error=False):
        def raw(request, folder):
            save(Path(folder) / 'bundle.json', self.bundle)
            if error:
                raise RuntimeError('provider unavailable')
            return copy.deepcopy(self.bundle)
        def supplement(archive, folder, ids, **kwargs):
            self.assertEqual(kwargs['http_limit'], 10)
            self.assertEqual(kwargs['browser_limit'], 4)
            save(Path(folder) / 'tasks/101/supplement.json', {'captures': {'https://example.org/b': {}},
                'attempts': [{'method': 'http', 'status': 'captured'}], 'remaining_gaps': [],
                'provider_calls': {'models': 0, 'tavily': 0, 'exa': 0}})
        def overlay(bundle, folder, ident):
            result = copy.deepcopy(bundle)
            result['pages']['https://example.org/b'] = {'content': 'A second independent report supplies the requested project dates. ' * 40}
            result['pages']['https://example.org/duplicate'] = {'content': self.body}
            result['pages']['https://example.org/blank'] = {'content': ''}
            result['supplement_lineage'] = {}
            return result
        return patch.multiple(pipeline, run_research=raw, supplement=supplement, analysis_overlay=overlay)

    def test_full_handoff_and_idempotent_resume(self):
        with self.collect():
            result = pipeline.collect(self.request, self.root)
        self.assertEqual(result['readable_unique_pages'], 2)
        self.assertEqual(result['excluded_pages'], 2)
        self.assertTrue(result['original_ledgers_preserved'])
        final = load(self.root / 'analysis-input.json')
        self.assertIn('https://example.org/b', final['pages'])
        self.assertEqual(final['searches'], self.bundle['searches'])
        with patch.object(pipeline, 'run_research', side_effect=AssertionError('repeat')):
            self.assertEqual(pipeline.collect(self.request, self.root), result)

    def test_no_result_preserved_and_reported(self):
        self.bundle['result'] = None
        with self.collect(error=True):
            result = pipeline.collect(self.request, self.root)
        self.assertFalse(result['collector_complete'])
        self.assertEqual(load(self.root / 'raw/bundle.json')['result'], None)
        self.assertTrue(any('incomplete' in gap for gap in result['gaps']))

    def test_limits_cannot_reset_and_raw_parent_tamper_fails(self):
        with self.collect():
            pipeline.collect(self.request, self.root)
        with self.assertRaises(ValueError):
            pipeline.collect(self.request, self.root, limits={**pipeline.DEFAULT_LIMITS, 'http': 9})
        changed = load(self.root / 'raw/bundle.json')
        changed['searches'] = []
        save(self.root / 'raw/bundle.json', changed)
        with self.assertRaises(ValueError):
            pipeline.collect(self.request, self.root)

    def test_empty_gap_list_does_not_hide_declared_stop_or_unread_candidates(self):
        self.bundle['result']['agent_declared_gaps'] = ['raw_source_budget_exhausted']
        self.bundle['result']['unread_urls'] = ['https://example.org/unread']
        with self.collect():
            result = pipeline.collect(self.request, self.root)
        self.assertEqual(result['state'], 'collected_with_gaps')
        self.assertEqual(result['acquisition_coverage']['unread_candidate_count'], 1)
        self.assertEqual(result['acquisition_coverage']['agent_declared_gaps'], ['raw_source_budget_exhausted'])
        self.assertFalse(result['acquisition_coverage']['full_recall_verified'])
        self.assertEqual(load(self.root / 'raw/bundle.json')['result']['gaps'], [])


if __name__ == '__main__':
    unittest.main()
