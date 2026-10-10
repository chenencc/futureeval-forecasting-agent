"""Source receipts and graph freshness agree without fabricating graph edits."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from ForecastAgent.research_loop import gap_feedback as gf, state, delta, post_supplement as post
from ForecastAgent.tests.test_research_gap_feedback import task, initial, receipt, patch
from ForecastAgent.analysis.pilot import digest


class ReviewConsistencyTests(unittest.TestCase):
    def irrelevant(self, t):
        url = 'https://example.org/unrelated'
        t.bundle['pages'][url] = {'content': 'A different issuer reported on a different event. ' * 30}
        t.execute('inspect_research_state', {'url': url, 'limit': 1}, 'unused')
        r = receipt(t, url, 'irrelevant', [], 'no_change')
        return t.execute('update_research_state', patch(t, [r]), 'unused')

    def test_receipt_only_review_clears_stale_without_changing_graph_or_target_gaps(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root); initial(t)
            graph = copy.deepcopy(t.bundle['research_loop'])
            budget = t.budget()
            outcome = self.irrelevant(t)
            self.assertFalse(outcome['committed'])
            self.assertTrue(outcome['material_acknowledged'])
            self.assertEqual(t.bundle['research_loop'], graph)
            self.assertEqual(t.budget(), budget)
            self.assertFalse(t.bundle['research_acquisition']['last_map_feedback']['requires_local_read'])
            audit = state.audit(t.bundle)
            self.assertEqual(audit['status'], 'bound_unverified')
            self.assertTrue(audit['graph_material_changed'])
            self.assertFalse(audit['unreviewed_material_change'])
            self.assertFalse(audit['pending_saved_material_review'])
            self.assertFalse(audit['truth_verified'])
            self.assertTrue(gf.gaps(t))  # Processing does not close the target gap.

    def test_stale_dispatch_latch_does_not_override_current_valid_receipts(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root); initial(t); self.irrelevant(t)
            t.bundle['research_acquisition']['pending_map_update'] = True
            graph = copy.deepcopy(t.bundle['research_loop'])
            self.assertFalse(state.audit(t.bundle)['pending_saved_material_review'])
            status = gf.reconcile(t)
            self.assertEqual(status['pending_material_count'], 0)
            self.assertFalse(t.bundle['research_acquisition']['pending_map_update'])
            self.assertEqual(t.bundle['research_loop'], graph)

    def test_new_body_or_unread_source_remains_pending_despite_false_latch(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root); initial(t)
            url = 'https://example.org/report'
            t.bundle['pages'][url]['content'] += ' New period: corrected value.'
            t.bundle['pages'][url].pop('content_sha256', None)
            t.bundle['research_acquisition']['pending_map_update'] = False
            self.assertEqual(gf.coverage(t)['pending_material_count'], 1)
            self.assertEqual(state.audit(t.bundle)['status'], 'invalid_bindings')
            t.bundle['pages']['https://example.org/new'] = {'content': 'Unread current source. ' * 30}
            self.assertEqual(gf.coverage(t)['pending_material_count'], 2)
            self.assertTrue(state.audit(t.bundle)['pending_saved_material_review'])

    def test_retired_incorporation_invalidates_its_old_processing_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root); initial(t)
            proposed = patch(t, [])
            proposed['revision_kind'] = 'interpretation_correction'
            proposed['retired_node_ids'] = ['observed_report']
            observed = next(n for n in t.bundle['research_loop']['current']['nodes']
                            if n['id'] == 'observed_report')
            replacement = delta.node_input(observed)
            replacement['id'] = 'replacement_observation'
            proposed['nodes'] = [replacement]
            t.execute('update_research_state', proposed, 'unused')
            self.assertEqual(gf.coverage(t)['pending'][0]['reason'], 'retired_or_changed_incorporation')
            self.assertTrue(state.audit(t.bundle)['pending_saved_material_review'])

    def test_changed_visible_scope_requires_its_own_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root); initial(t)
            span = next(iter(state.catalog(t.bundle)['spans'].values()))
            t.bundle['research_map_visible_references'] = [{
                'url': span['url'], 'body_sha256': span['body_sha256'],
                'start': span['start'], 'end': span['end'] - 1}]
            self.assertEqual(gf.coverage(t)['pending_material_count'], 1)
            self.assertEqual(gf.coverage(t)['pending'][0]['reason'], 'no_current_receipt')
            self.assertTrue(state.audit(t.bundle)['pending_saved_material_review'])

    def test_changed_duplicate_parent_invalidates_duplicate_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root); initial(t)
            related = next(iter(gf.materials(t)))
            url = 'https://example.org/reprint'
            t.bundle['pages'][url] = copy.deepcopy(t.bundle['pages']['https://example.org/report'])
            t.execute('inspect_research_state', {'url': url, 'limit': 1}, 'unused')
            r = receipt(t, url, 'duplicate', [], 'no_change'); r['related_material_ids'] = [related]
            t.execute('update_research_state', patch(t, [r]), 'unused')
            self.assertEqual(gf.coverage(t)['pending_material_count'], 0)
            t.bundle['pages']['https://example.org/report']['content'] += ' Changed original.'
            t.bundle['pages']['https://example.org/report'].pop('content_sha256', None)
            reasons = {r['reason'] for r in gf.coverage(t)['pending']}
            self.assertIn('stale_duplicate_parent', reasons)

    def test_deferred_is_pending_and_corrupt_journal_is_not_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root); initial(t)
            url = 'https://example.org/report'
            r = receipt(t, url, 'deferred', [], 'unknown')
            t.execute('update_research_state', patch(t, [r]), 'unused')
            self.assertEqual(gf.coverage(t)['pending'][0]['reason'], 'deferred')
            t.bundle['research_gap_feedback']['events'][-1]['model_explanation'] = 'tampered'
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                state.audit(t.bundle)

    def test_zero_pending_post_review_needs_no_model_and_preserves_original_graph(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(Path(root)/'parent'); initial(t); self.irrelevant(t)
            original = copy.deepcopy(t.bundle); before = digest(original)
            with mock_patch('ForecastAgent.research_loop.post_supplement.ask_model',
                            side_effect=AssertionError('No model needed')) as model:
                child, report = post.run(original, Path(root)/'review', execute=True)
            model.assert_not_called()
            self.assertEqual(digest(original), before)
            self.assertEqual(child['research_loop'], original['research_loop'])
            self.assertEqual(child['research_gap_feedback'], original['research_gap_feedback'])
            self.assertFalse(child['research_acquisition']['pending_map_update'])
            self.assertEqual(report['map_audit']['status'], 'bound_unverified')
            self.assertEqual(report['status'], 'already_processed')
            self.assertEqual(report['usage']['totals']['http_attempts'], 0)
            for key in post.PRESERVED:
                self.assertEqual(child.get(key), original.get(key))
