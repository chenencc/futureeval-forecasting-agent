"""Local source scope, ambiguous repeats and equal experimental arm coverage."""
import base64
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import source_scope, release_paired_trial as trial
from ForecastAgent.tests import test_research_release_pair as pair_fixture
from ForecastAgent.tests.test_competition_mercury import bundle


def snapshot(html):
    raw = html.encode()
    return {'url': 'https://example.org/events/2026', 'content_type': 'text/html',
        'raw_response_base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()}


class SourceScopeTests(unittest.TestCase):
    def test_past_result_retains_its_heading_and_link_without_inferred_target_label(self):
        page = snapshot('<main><h1>Current event</h1><section><h2>Current expectations</h2><p>Expected total 42.</p></section>'
            '<section><h2>Previous event</h2><a href="/events/2024"><span>20 points</span><span>26 points</span><span>FINAL</span></a></section></main>')
        before = copy.deepcopy(page)
        result = source_scope.scopes(page, 'Expected total 42. 20 points 26 points FINAL')
        history = next(c for c in result['contexts'] if any(a['href_attribute'] == '/events/2024' for a in c['ancestors']))
        self.assertTrue(any(any(h['text'] == 'Previous event' for h in a['headings']) for a in history['ancestors']))
        self.assertFalse(history['event_identity_verified'])
        self.assertFalse(history['target_applicability_verified'])
        self.assertEqual(result['network_calls'], 0)
        self.assertEqual(page, before)

    def test_identical_value_in_two_periods_keeps_both_scopes(self):
        page = snapshot('<main><section><h2>Q1 2025</h2><p>Revenue 24 billion.</p></section>'
            '<section><h2>Q1 2026</h2><p>Revenue 24 billion.</p></section></main>')
        result = source_scope.scopes(page, 'Revenue 24 billion.')
        headings = {h['text'] for c in result['contexts'] for a in c['ancestors'] for h in a['headings']}
        self.assertEqual(headings, {'Q1 2025', 'Q1 2026'})
        self.assertEqual(len(result['contexts']), 2)

    def test_navigation_and_sibling_headings_do_not_become_scope(self):
        page = snapshot('<nav><h2>Menu</h2><p>Revenue 24 billion.</p></nav><main>'
            '<section><h2>Historical result</h2><p>Unrelated past result.</p></section>'
            '<section><h2>Current guidance</h2><p>Revenue 24 billion.</p></section></main>')
        result = source_scope.scopes(page, 'Revenue 24 billion.')
        headings = {h['text'] for c in result['contexts'] for a in c['ancestors'] for h in a['headings']}
        self.assertEqual(headings, {'Current guidance'})

    def test_responsive_duplicates_keep_occurrence_proofs_without_duplicate_scopes(self):
        section = '<section><h2>Previous event</h2><a href="/events/2024"><span>20 points</span></a></section>'
        result = source_scope.scopes(snapshot('<main>'+section+section+'</main>'), '20 points')
        self.assertEqual(len(result['contexts']), 1)
        self.assertEqual(len(result['contexts'][0]['scope_occurrences']), 2)
        self.assertFalse(result['contexts'][0]['event_identity_verified'])

    def test_corrupt_raw_capture_is_preserved_as_unavailable_not_bound(self):
        page = snapshot('<section><h2>Result</h2><p>Revenue 24 billion.</p></section>')
        page['sha256'] = 'wrong'
        common = {'sources': [{'source_id': 'a', 'url': page['url']}],
            'evidence': [{'source_id': 'a', 'text': 'Revenue 24 billion.'}]}
        result, audit = source_scope.attach({'pages': {page['url']: page}}, common)
        self.assertEqual(result, common)
        self.assertEqual(audit['skipped'][0]['reason'], 'structure_unavailable')
        self.assertEqual(audit['network_calls'], 0)

    def test_scope_size_cap_never_removes_original_text(self):
        page = snapshot('<section><h2>Result</h2><p>Revenue 24 billion.</p></section>')
        common = {'sources': [{'source_id': 'a', 'url': page['url']}],
            'evidence': [{'source_id': 'a', 'text': 'Revenue 24 billion.'}]}
        with patch.object(source_scope, 'BYTE_CAP', 1):
            result, audit = source_scope.attach({'pages': {page['url']: page}}, common)
        self.assertEqual(result, common)
        self.assertEqual(audit['skipped'][0]['reason'], 'context_byte_cap')

    def test_both_release_arms_get_equal_context_and_originals(self):
        states = []; raw = bundle()
        sideband = {'schema': source_scope.VERSION, 'sources': [{'raw_qualifier': 'Previous event'}]}
        fixture = pair_fixture.ReleasePairTests()
        def attach(bundle, original, **kwargs):
            return {**copy.deepcopy(original), 'source_scope_context': copy.deepcopy(sideband)}, {'sideband_present': True}
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), \
                patch.object(source_scope, 'attach', side_effect=attach), \
                patch('ForecastAgent.providers.decisions.decide', fixture.provider(states)), \
                patch.object(trial, 'mapped_state', fixture.mapped):
            result = trial.paired_release(raw, raw, Path(tmp), source_context=True)
        self.assertEqual(states[0]['source_scope_context'], states[1]['source_scope_context'])
        self.assertEqual(states[0], {k: v for k, v in states[1].items() if k != 'research_map'})
        self.assertTrue(result['stages'][0]['source_context_audit']['sideband_present'])
        self.assertEqual(result['original']['probability_yes'], .3)

    def test_model_context_keeps_qualifiers_and_archives_coordinate_proofs(self):
        page = snapshot('<section><h2>Previous quarter</h2><a href="/reports/2025"><p>Revenue 24 billion.</p></a></section>')
        common = {'sources': [{'source_id': 'a', 'url': page['url']}],
            'evidence': [{'source_id': 'a', 'text': 'Revenue 24 billion.'}]}
        state, audit = source_scope.attach({'pages': {page['url']: page}}, common, byte_cap=1800)
        visible = state['source_scope_context']['sources'][0]['contexts'][0]
        self.assertEqual(visible['href_attributes'], ['/reports/2025'])
        self.assertEqual(visible['headings'][0]['text'], 'Previous quarter')
        self.assertNotIn('xpath', visible['headings'][0])
        self.assertTrue(audit['bound_contexts'][0]['contexts'][0]['ancestors'][0]['xpath'])
        import json
        self.assertLessEqual(len(json.dumps(state['source_scope_context']).encode()), 1800)

    def test_first_stage_context_respects_native_request_cap_without_removing_originals(self):
        from ForecastAgent.analysis import mercury_evidence_chain as chain
        original = {'evidence': [{'text': 'preserved'}]}
        seen = []
        def bounded(bundle, state, **kwargs):
            seen.append(kwargs['byte_cap'])
            return copy.deepcopy(state), {'sideband_present': False}
        with patch.object(source_scope, 'attach', side_effect=bounded), \
                patch.object(chain, 'request_bytes', return_value=chain.FIRST_BYTES-100):
            result, _ = trial.contextual_original({}, original, {})
        self.assertEqual(seen, [68])
        self.assertEqual(result, original)


if __name__ == '__main__':
    unittest.main()
