"""Offline acceptance against the actual immutable production analysis code."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

if '--release' in sys.argv:
    position = sys.argv.index('--release')
    sys.path.insert(0, str(Path(sys.argv[position+1]).resolve()))
    del sys.argv[position:position+2]
from competition_context import project, wrap_analyze, reconcile, ERROR, POLICY
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.competition import mercury
from ForecastAgent.tests.test_competition_mercury import bundle, response
from ForecastAgent.releases import context_decisions


class ContextTests(unittest.TestCase):
    def test_types_rules_and_clocks_survive_internal_noise(self):
        for kind in ['binary', 'multiple_choice', 'numeric', 'date', 'discrete']:
            original = bundle(kind)
            original['request']['capability_code_identity'] = {'internal': 'x'*30000}
            original['request']['predictive_information_contract'] = {'derived': 'y'*20000}
            frozen = copy.deepcopy(original)
            clean = project(original)
            self.assertEqual(clean['request'], bundle(kind)['request'])
            self.assertEqual(clean['pages'], original['pages'])
            self.assertEqual(original, frozen)
            spec = None if kind == 'binary' else mercury.distribution_spec(clean['request'])
            registry = context_decisions.chain.questions() if spec is None else context_decisions.typed.questions(spec)
            first, audit = context_decisions.select_first(clean, registry)
            self.assertTrue(first['evidence'])
            self.assertLessEqual(context_decisions.chain.request_bytes(first, registry), 22000)

    def test_labels_rejected_before_projection(self):
        original = bundle(); original['request']['resolution'] = 'yes'
        with self.assertRaises(ValueError): project(original)

    def test_real_frozen_analysis_scores_and_replays_without_new_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = bundle('multiple_choice')
            original['request']['capability_code_identity'] = {'implementation': 'x'*30000}
            save(root/'input.json', original)
            old = root/'analysis/mercury-v1.0.1'
            # Match a genuine pre-HTTP failure layout without editing its files.
            packet = mercury.packet_for(original)
            save(old/'packet.json', packet)
            save(old/'identity.json', {'packet_sha256': context_decisions.digest(packet)})
            before = {p: p.read_bytes() for p in old.rglob('*') if p.is_file()}
            def decide(state, registry, key, journal):
                record = {'status': 'reserved'}; token = journal('reserve', record)
                result = response(registry); record.update(status='received', response=result)
                journal('complete', record, token)
                return result
            adapter = wrap_analyze(context_decisions.analyze)
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch('ForecastAgent.providers.decisions.decide', side_effect=decide) as model:
                first = adapter(root/'input.json', root/'analysis', '7')
                again = adapter(root/'input.json', root/'analysis', '7')
            self.assertEqual(first, again)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(load(root/'input.json'), original)
            self.assertEqual({p: p.read_bytes() for p in before}, before)
            self.assertAlmostEqual(sum(first['payload']['probability_yes_per_category'].values()), 1)

    def test_existing_http_never_gets_a_new_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = bundle()
            original['request']['capability_code_identity'] = {'x': 'large'}
            save(root/'input.json', original)
            save(root/'analysis/mercury-v1.0.1/first/http/001.json', {'status': 'reserved'})
            calls = []
            wrapped = wrap_analyze(lambda source, folder, ident: calls.append(folder))
            wrapped(root/'input.json', root/'analysis', '7')
            self.assertEqual(calls, [root/'analysis'])
            self.assertFalse((root/'analysis'/POLICY).exists())

    def test_recovery_keeps_deadline_and_counters(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {'id': '7', 'stage': 'needs_review', 'last_error': ERROR,
                'deadline_utc': '2026-10-15T17:00:00Z', 'recoverable_failures': 3,
                'collection_executions': 1, 'history': []}
            save(root/'campaign.json', {'tasks': {'7': task}})
            receipts = reconcile(root, datetime(2026, 10, 16, tzinfo=timezone.utc))
            result = load(root/'campaign.json')['tasks']['7']
            self.assertEqual(result['stage'], 'deadline_missed')
            self.assertEqual(result['last_error'], ERROR)
            self.assertEqual(result['recoverable_failures'], 3)
            self.assertEqual(result['collection_executions'], 1)
            self.assertFalse(receipts[0]['budgets_reset'])
            self.assertEqual(reconcile(root), [])

    def test_saved_submission_is_never_reopened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {'id': '7', 'stage': 'needs_review', 'last_error': ERROR,
                'deadline_utc': '2020-01-01T00:00:00Z'}
            save(root/'campaign.json', {'tasks': {'7': task}})
            save(root/'tasks/7/submission.json', {'status': 'unknown'})
            self.assertEqual(reconcile(root), [])
            self.assertEqual(load(root/'campaign.json')['tasks']['7'], task)


if __name__ == '__main__': unittest.main()
