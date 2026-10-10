"""Exercise decision/HTTP limits through the collector without provider traffic."""
import copy
from contextlib import ExitStack
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.runtime import retrieval
from ForecastAgent.tests.test_collection import call
from ForecastAgent.tests.test_research_dispatch import fresh


class DecisionBudgetTests(unittest.TestCase):
    def run_case(self, root, *, decisions=26, http=34, recovery_branch='errors',
                 retries=0, initial_attempts=0, finish=False, seconds=900):
        original = fresh(root)
        task = retrieval.RetrievalTask(original.directory / 'counter',
            {**original.bundle['request'], 'acquisition_focus':'raw_recall'})
        task.bundle['pages'] = copy.deepcopy(original.bundle['pages'])
        task.bundle['plan'] = copy.deepcopy(original.bundle['plan'])
        from ForecastAgent.research_loop import fusion
        fusion.initialize(task)
        task.bundle['control']['consecutive_errors'] = 3
        task.bundle['model_attempts'] = [
            {'id':i + 1, 'status':'received'} for i in range(initial_attempts)]
        task.save()
        captures = copy.deepcopy({k:task.bundle[k] for k in
            ['pages', 'searches', 'exa_searches', 'fetch_attempts']})
        responses = []

        def closure_ready(task):
            if recovery_branch == 'loop':
                # Simulate a broken local dispatcher repeatedly losing its latch.
                task.bundle['control']['source_recovery_delivery_pending'] = False
            return False

        def respond(messages, key, **options):
            observer = options['observer']
            for i in range(retries + 1):
                record = {'started_at_utc':'2026-10-10T00:00:00Z',
                          'retry_index':i, 'status':'reserved'}
                token = observer('reserve', record)
                observer('complete', {**record, 'status':
                    'transport_error' if i < retries else 'received'}, token)
            response = call('finish_collection' if finish else 'list_sources',
                            {'gaps':[]} if finish else {}, f'model-{len(responses)+1}')
            responses.append(response)
            return response

        with ExitStack() as stack:
            stack.enter_context(patch.object(retrieval, 'COLLECTION_MAX_TURNS', decisions))
            stack.enter_context(patch.object(retrieval, 'COLLECTION_HTTP_PER_DISPATCH', http))
            stack.enter_context(patch.object(retrieval, 'MODEL_FAILURES_PER_DISPATCH', 40))
            stack.enter_context(patch.object(retrieval, 'MAX_RUN_SECONDS', seconds))
            stack.enter_context(patch('urllib.request.urlopen', side_effect=AssertionError('No network')))
            model = stack.enter_context(patch.object(retrieval, 'ask_ultra', side_effect=respond))
            recovery = stack.enter_context(patch(
                'ForecastAgent.runtime.source_frontier.recover_before_stall',
                return_value={'status':'completed', 'model_calls':0, 'search_calls':0}))
            stack.enter_context(patch('ForecastAgent.runtime.source_frontier.rescue_before_close', return_value=None))
            stack.enter_context(patch('ForecastAgent.runtime.collection_actions.raw_stop_reason',
                return_value='raw_no_progress_limit' if recovery_branch == 'loop' else None,
                side_effect=([ 'raw_no_progress_limit'] + [None]*100)
                if recovery_branch == 'raw' else None))
            stack.enter_context(patch('ForecastAgent.runtime.material_protocol.closure_ready', side_effect=closure_ready))
            stack.enter_context(patch('ForecastAgent.research_loop.dispatch.choose', return_value=None))
            stack.enter_context(patch('ForecastAgent.research_loop.fusion.local_cycle', return_value=True))
            stack.enter_context(patch('ForecastAgent.research_loop.fusion.advisory_forcing', return_value=None))
            stack.enter_context(patch('ForecastAgent.runtime.tool_selection.active_tools',
                                     side_effect=lambda task, tools, forced:tools))
            bundle = retrieval.run_retrieval(task.bundle['request'], task.directory, '', 'fixture')
        for key, value in captures.items():
            self.assertEqual(bundle[key], value)
        return bundle, model.call_count, recovery.call_count

    def test_program_recovery_leaves_all_26_decisions_available(self):
        for branch in ['errors', 'raw']:
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as root:
                b, calls, recoveries = self.run_case(root, recovery_branch=branch)
                s = b['sessions'][-1]
                self.assertEqual(calls, 26)
                self.assertEqual(recoveries, 1)
                self.assertEqual(s['model_decisions'], 26)
                self.assertEqual(s['program_steps'], 1)
                self.assertEqual(s['scheduler_iterations'], 27)
                self.assertEqual([t['turn'] for t in s['turns']], list(range(1, 27)))
                self.assertEqual(s['exhausted_budget'], 'model_decisions')
                self.assertEqual(len(b['model_attempts']), 26)
                self.assertTrue(all(not t['failed'] for t in s['turns']))
                self.assertEqual(b['result']['termination_reason'], 'program_dispatch_limit')
                self.assertTrue(b['result']['resumable'])

    def test_transport_retries_spend_http_but_not_received_decisions(self):
        with tempfile.TemporaryDirectory() as root:
            b, calls, _ = self.run_case(root, decisions=6, http=4, retries=1)
            self.assertEqual(calls, 2)
            self.assertEqual(b['sessions'][-1]['model_decisions'], 2)
            self.assertEqual(len(b['model_attempts']), 4)
            self.assertEqual(b['result']['termination_reason'], 'model_dispatch_budget')

    def test_existing_lifetime_attempts_are_not_reset(self):
        with tempfile.TemporaryDirectory() as root:
            b, calls, _ = self.run_case(root, decisions=6, http=12, initial_attempts=71)
            self.assertEqual(calls, 1)
            self.assertEqual(len(b['model_attempts']), 72)
            self.assertEqual(b['result']['termination_reason'], 'lifetime_model_budget')
            self.assertFalse(b['result']['resumable'])

    def test_explicit_finish_does_not_fill_remaining_quota(self):
        with tempfile.TemporaryDirectory() as root:
            b, calls, _ = self.run_case(root, finish=True)
            self.assertEqual(calls, 1)
            self.assertEqual(b['sessions'][-1]['model_decisions'], 1)
            self.assertNotEqual(b['result']['termination_reason'], 'program_dispatch_limit')

    def test_zero_decisions_make_no_provider_request(self):
        with tempfile.TemporaryDirectory() as root:
            b, calls, recoveries = self.run_case(root, decisions=0)
            self.assertEqual(calls, 0)
            self.assertEqual(recoveries, 0)
            self.assertEqual(b['sessions'][-1]['exhausted_budget'], 'model_decisions')

    def test_deadline_precedes_recovery_and_model_calls(self):
        with tempfile.TemporaryDirectory() as root:
            b, calls, recoveries = self.run_case(root, seconds=0)
            self.assertEqual(calls, 0)
            self.assertEqual(recoveries, 0)
            self.assertEqual(b['result']['termination_reason'], 'deadline')

    def test_program_only_loop_has_its_own_guard(self):
        with tempfile.TemporaryDirectory() as root:
            b, calls, recoveries = self.run_case(root, recovery_branch='loop')
            self.assertEqual(calls, 0)
            self.assertEqual(recoveries, 26)
            self.assertEqual(b['sessions'][-1]['model_decisions'], 0)
            self.assertEqual(b['sessions'][-1]['exhausted_budget'], 'program_steps')
            self.assertTrue(b['result']['resumable'])

    def test_resume_retains_old_session_and_physical_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            b, _, _ = self.run_case(root, decisions=4)
            original_session = copy.deepcopy(b['sessions'][0])
            original_attempts = copy.deepcopy(b['model_attempts'])
            directory = retrieval.Path(root) / 'candidate' / 'counter'

            def finish(messages, key, **options):
                record = {'started_at_utc':'2026-10-10T00:00:00Z',
                          'retry_index':0, 'status':'reserved'}
                token = options['observer']('reserve', record)
                options['observer']('complete', {**record, 'status':'received'}, token)
                return call('finish_collection', {'gaps':[]}, 'resume-finish')

            with patch.object(retrieval, 'ask_ultra', side_effect=finish) as model, \
                 patch('urllib.request.urlopen', side_effect=AssertionError('No network')), \
                 patch('ForecastAgent.runtime.collection_actions.raw_stop_reason', return_value=None), \
                 patch('ForecastAgent.runtime.material_protocol.closure_ready', return_value=False), \
                 patch('ForecastAgent.research_loop.dispatch.choose', return_value=None), \
                 patch('ForecastAgent.research_loop.fusion.advisory_forcing', return_value=None), \
                 patch('ForecastAgent.runtime.tool_selection.active_tools',
                       side_effect=lambda task, tools, forced:tools):
                resumed = retrieval.run_retrieval(b['request'], directory, '', 'fixture')
            self.assertEqual(model.call_count, 1)
            self.assertEqual(resumed['sessions'][0], original_session)
            self.assertEqual(resumed['sessions'][-1]['model_decisions'], 1)
            self.assertEqual(resumed['model_attempts'][:4], original_attempts)
            self.assertEqual(len(resumed['model_attempts']), 5)


if __name__ == '__main__':
    unittest.main()
