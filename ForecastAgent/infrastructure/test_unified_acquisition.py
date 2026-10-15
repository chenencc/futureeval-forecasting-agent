"""Regression gates for the real common entry and typed competition bridge."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE/(name+'.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result
core = module('unified_acquisition')
host = module('unified_competition')


class HostBridge(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.calls = []
        self.request = {'id': '123', 'question': 'Exact original question?', 'question_type': 'binary',
            'resolution_criteria': 'Exact predicate and source.', 'fine_print': 'Exact exceptions.',
            'mode': 'live', 'as_of_utc': '2026-10-15T00:00:00Z'}
        self.body = 'Original body'; self.status = 'completed'
        def runner(command, log, timeout, cwd=None):
            self.calls.append((command, timeout))
            if self.status != 'completed': return {'status': self.status, 'returncode': -1}
            req = core.load(command[command.index('--request')+1])
            directory = Path(command[command.index('--directory')+1])
            data = {'request': req, 'result': {'status': 'partial', 'native_marker': 'unchanged'},
                'pages': {'https://example.org': {'content': self.body,
                    'body_diagnostics': {'usable_text': bool(self.body)}}}, 'research_state': {'nodes': []}}
            core.save(directory/'package.json', data)
            core.save(directory/'package-integrity.json', {'sha256': core.sha(directory/'package.json')})
            return {'status': 'completed', 'returncode': 0}
        self.collector = host.Collector(self.root, self.root/'core', self.root/'engine', 'a'*40, runner)
        self.folder = self.root/'tasks/123'; self.retrieval = self.folder/'retrieval'

    def test_same_callable_and_pins_in_child(self):
        self.collector.collect(self.request, self.retrieval)
        command = self.calls[0][0]
        self.assertEqual(command[command.index('--expected-core')+1], 'a'*40)
        self.assertIn('unified_acquisition.py', command[2])

    def test_binary_and_all_nonbinary_contract_fields_preserved(self):
        for kind in ['binary', 'multiple_choice', 'numeric', 'date', 'discrete']:
            req = dict(self.request, question_type=kind)
            if kind == 'multiple_choice': req['options'] = ['A', 'B']
            if kind in ['numeric', 'date', 'discrete']:
                req.update(scaling={'range_min': 0, 'range_max': 10}, inbound_outcome_count=10,
                           open_lower_bound=True, open_upper_bound=False, unit='exact unit')
            retrieval = self.root/kind/'retrieval'
            bridge = self.collector.collect(req, retrieval)
            result = self.collector.supplement(bridge, self.root/kind, req['id'])
            for field, value in req.items():
                if field != 'as_of_utc': self.assertEqual(result['request'][field], value)
            self.assertEqual(result['result']['native_marker'], 'unchanged')

    def test_replay_does_not_dispatch_or_refresh_clock(self):
        first = self.collector.collect(self.request, self.retrieval)
        second = self.collector.collect(dict(self.request, as_of_utc='2026-10-16T00:00:00Z'), self.retrieval)
        self.assertEqual(first, second); self.assertEqual(len(self.calls), 1)

    def test_supplement_reuses_package_without_new_budget(self):
        bridge = self.collector.collect(self.request, self.retrieval)
        self.collector.supplement(bridge, self.folder, '123')
        self.collector.supplement(bridge, self.folder, '123')
        self.assertEqual(len(self.calls), 1)

    def test_changed_rule_or_option_blocks_replay(self):
        self.collector.collect(self.request, self.retrieval)
        with self.assertRaises(ValueError):
            self.collector.collect(dict(self.request, resolution_criteria='Changed rule'), self.retrieval)

    def test_changed_package_blocks_without_relaunch(self):
        self.collector.collect(self.request, self.retrieval)
        core.save(self.retrieval/'unified-agent/package.json', {'forged': True})
        with self.assertRaises(ValueError): self.collector.collect(self.request, self.retrieval)
        self.assertEqual(len(self.calls), 1)

    def test_wrong_supplement_identity_blocks(self):
        bridge = self.collector.collect(self.request, self.retrieval)
        with self.assertRaises(ValueError): self.collector.supplement(bridge, self.folder, '999')

    def test_legacy_material_never_gets_new_quotas(self):
        core.save(self.retrieval/'old-bundle.json', {'old': True})
        with self.assertRaises(ValueError): self.collector.collect(self.request, self.retrieval)
        self.assertFalse(self.calls)

    def test_interrupted_child_preserves_original_contract(self):
        self.status = 'timed_out'
        with self.assertRaises(RuntimeError): self.collector.collect(self.request, self.retrieval)
        original = core.load(self.retrieval/'original-contract.json')
        self.status = 'completed'; self.collector.collect(self.request, self.retrieval)
        self.assertEqual(original, core.load(self.retrieval/'original-contract.json'))
        self.assertEqual(len(self.calls), 2)

    def test_empty_material_is_not_success(self):
        self.body = ''
        with self.assertRaises(RuntimeError): self.collector.collect(self.request, self.retrieval)
        self.assertTrue((self.retrieval/'unified-agent/package.json').exists())
        self.assertFalse((self.retrieval/'unified-bridge.json').exists())

    def test_child_timeout_precedes_host_deadline(self):
        import time
        with patch.dict(os.environ, {'FORECAST_TASK_PROCESS_DEADLINE_EPOCH': str(time.time()+100)}):
            self.collector.collect(self.request, self.retrieval)
        self.assertLessEqual(self.calls[0][1], 70)


class OldUsage(unittest.TestCase):
    def test_local_day_wait_preserves_receipts_without_consuming_lifetime_execution(self):
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        error = host.AcquisitionBudgetHold({'scope': 'utc_day', 'transmitted': False,
            'retry_at': '2026-10-16T00:00:00Z'}, 'attempt-1')
        task = {'collection_executions': 2, 'recoverable_failures': 1}
        stage, retry = host.classify_budget_hold(error, task, now, lambda *a: self.fail())
        self.assertEqual(stage, 'retry_wait')
        self.assertTrue(retry.startswith('2026-10-16'))
        self.assertEqual(task['collection_executions'], 1)
        self.assertEqual(task['recoverable_failures'], 1)
        host.classify_budget_hold(error, task, now, lambda *a: self.fail())
        self.assertEqual(task['collection_executions'], 1)

    def test_lifetime_exhaustion_remains_terminal(self):
        error = host.AcquisitionBudgetHold({'scope': 'task_lifetime'}, 'attempt-1')
        task = {'collection_executions': 2}
        self.assertEqual(host.classify_budget_hold(error, task, datetime.now(timezone.utc), None),
            ('provider_blocked', None))
        self.assertEqual(task['collection_executions'], 2)
    def test_previous_primary_and_backup_attempts_charged_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); budget = root/'unified-provider'; ledger = {}
            for index in range(2):
                core.save(root/f'provider-transport/openrouter/attempts/{index}.json',
                    {'provider': 'openrouter', 'started_at_utc': '2026-10-15T01:00:00Z', 'state': 'reserved'})
            core.save(root/'exa-transport/attempts/3.json', {'started_at_utc': '2026-10-15T01:00:00Z'})
            core.import_prior_usage(ledger, budget); core.import_prior_usage(ledger, budget)
            self.assertEqual(len(ledger['provider_reservations']), 3)
            self.assertEqual(sum(r['kind']=='model' for r in ledger['provider_reservations']), 2)
            self.assertTrue(all(r['day']=='2026-10-15' for r in ledger['provider_reservations']))

    def test_unknown_old_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); core.save(root/'provider-transport/openrouter/attempts/0.json', {'bad': True})
            with self.assertRaises(ValueError): core.import_prior_usage({}, root/'unified-provider')

    def test_imported_receipts_keep_identity_after_moving_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            core.save(root/'first/provider-transport/openrouter/attempts/0.json',
                {'provider': 'openrouter', 'started_at_utc': '2026-10-15T01:00:00Z'})
            ledger = {}
            core.import_prior_usage(ledger, root/'first/unified-provider')
            shutil.copytree(root/'first', root/'restored')
            core.import_prior_usage(ledger, root/'restored/unified-provider')
            self.assertEqual(len(ledger['provider_reservations']), 1)

    def test_provider_script_target_keeps_arguments_and_current_release_path(self):
        transport = module('provider_failover')
        fake = type('Transport', (), {'credentials': {}, '_forecast_exa_transport': True})()
        received = []
        with patch.object(transport, 'setup_bootstrap'), patch.object(transport, 'install'), \
                patch.object(transport.urllib.request, 'urlopen', fake), \
                patch.object(transport.runpy, 'run_path', lambda path, **kw: received.append(
                    (path, list(sys.argv), kw))), patch.dict(os.environ), \
                patch.object(sys, 'path', list(sys.path)), \
                patch.object(sys, 'argv', ['provider_failover.py', '--script', str(HERE/'unified_competition.py'),
                    '--', '--root', 'fixture', '--once']):
            transport.main()
        self.assertEqual(received[0][1][1:], ['--root', 'fixture', '--once'])
        self.assertEqual(received[0][2], {'run_name': '__main__'})

    def test_freeze_cannot_rewrite_an_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'identity.json'; core.freeze(path, {'old': 1})
            with self.assertRaises(ValueError): core.freeze(path, {'old': 2})
            self.assertEqual(core.load(path), {'old': 1})


if __name__ == '__main__': unittest.main()
