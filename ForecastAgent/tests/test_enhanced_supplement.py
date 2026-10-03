"""Offline contracts for shared quotas, semantic gaps and bounded evidence reading."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load, digest
from ForecastAgent.supplement import enhanced as e
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.priority_reading import select


def bundle():
    return {'request': {'id': 1, 'question': 'Will Linux desktop share exceed 8% in August 2026?',
            'resolution_criteria': 'Use https://example.org/desktop for worldwide desktop Linux share.'},
            'pages': {}, 'searches': [], 'exa_searches': [], 'fetch_attempts': []}


class EnhancedTests(unittest.TestCase):
    def test_shell_and_month_are_distinct(self):
        shell = 'We respect your privacy and will never share your email address. See why over 1,000,000 bloggers use Statcounter to grow their business.'
        self.assertFalse(e.assess(bundle()['request'], 'https://example.org', shell)['eligible_for_evidence'])
        context = 'September 2026 worldwide desktop Linux share was 4.54%. '
        self.assertTrue(e.assess(bundle()['request'], 'https://example.org', context)['eligible_for_evidence'])
        self.assertEqual(e.assess(bundle()['request'], 'https://example.org', context)['coverage_status'], 'gap_or_context')
        # An article about consent remains evidence when it contains actual data.
        self.assertTrue(e.assess(bundle()['request'], 'https://example.org', shell+' August 2026 Linux share was 8.4%.')['eligible_for_evidence'])

    def test_reservations_and_restart_are_shared(self):
        b = bundle(); b['searches'] = [{'status': 'failed'}]*3; b['exa_searches'] = [{'status': 'reserved'}]
        before = copy.deepcopy(b)
        with tempfile.TemporaryDirectory() as tmp:
            page = {'content': 'August 2026 worldwide desktop Linux share 8.4%.', 'body_diagnostics': {'usable_text': True}}
            with patch.object(e, 'fetch_document', return_value=page) as http, patch.object(e, 'render_page') as browser:
                e.run(b, tmp, network=True); e.run(b, tmp, network=True)
                self.assertEqual(http.call_count, 1); self.assertEqual(browser.call_count, 0)
            self.assertEqual(b, before)
            self.assertEqual(load(Path(tmp)/'report.json')['usage']['tavily'], 3)
            with self.assertRaisesRegex(ValueError, 'Frozen'):
                e.run({**b, 'searches': []}, tmp)

    def test_reserved_free_calls_and_failed_captures_are_not_repeated(self):
        b = bundle(); b['fetch_attempts'] = [{}]*15
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(e, 'fetch_document', side_effect=RuntimeError('blocked')) as http, patch.object(e, 'render_page', side_effect=RuntimeError('blocked')) as browser:
                e.run(b, tmp, prior=[{'attempts': [{'method': 'browser'}]*6}], network=True)
                e.run(b, tmp, prior=[{'attempts': [{'method': 'browser'}]*6}], network=True)
                self.assertEqual(http.call_count, 1); self.assertEqual(browser.call_count, 0)
            self.assertEqual(load(Path(tmp)/'report.json')['usage']['http'], 16)

    def test_no_labels_and_rule_primary_retained(self):
        b = bundle(); p = e.plan(b)
        b['resolution'] = 'yes'; b['forecast'] = .02
        self.assertEqual(p, e.plan(b)); self.assertTrue(p['sources'][0]['rule_primary'])

    def test_exhausted_search_never_called(self):
        b = bundle(); b['request']['resolution_criteria'] = 'Use worldwide desktop Linux share.'
        b['searches'] = [{}]*3; b['exa_searches'] = [{}]
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            e.run(b, tmp, network=True, search=lambda *args: calls.append(args))
            self.assertEqual(calls, [])

    def test_critical_tail_is_selected_and_hash_checked(self):
        b = bundle()
        for i in range(8):
            b['pages'][f'https://example.org/{i}'] = {'content': ('Navigation background unrelated text. '*70)+ '\nAugust 2026 worldwide desktop Linux share was 8.4%.', 'body_diagnostics': {'usable_text': True}}
        packet = chain.full_packet(b); hints = e.hints(b)
        state, audit = select(packet, hints)
        self.assertTrue(any('8.4%' in s['text'] for s in state['evidence']))
        self.assertLessEqual(audit['request_bytes'], chain.FIRST_BYTES)
        self.assertTrue(all(s['text'] == b['pages'][s['url']]['content'][s['start']:s['end']] for s in state['evidence']))
        hints['spans'][0]['start'] += 1
        with self.assertRaisesRegex(ValueError, 'provenance'):
            select(packet, hints)

    def test_priority_hook_all_platform_types_and_no_second_request_on_resume(self):
        from ForecastAgent.tests.test_competition_mercury import bundle as typed_bundle, response
        from ForecastAgent.competition import mercury
        for kind in ('binary', 'multiple_choice', 'numeric', 'discrete', 'date'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                b = typed_bundle(kind); reading = e.hints(b)
                with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch.object(chain, 'call', return_value=response(chain.questions()) if kind == 'binary' else response(__import__('ForecastAgent.analysis.mercury_nonbinary_trial', fromlist=['questions']).questions(mercury.distribution_spec(b['request'])))):
                    mercury.run(b, Path(tmp), reading_hints=reading)
                    first = load(Path(tmp)/'first-input-audit.json')
                    self.assertIn('priority_selected_ids', first)
                    self.assertLessEqual(first['request_bytes'], chain.FIRST_BYTES)


if __name__ == '__main__':
    unittest.main()
