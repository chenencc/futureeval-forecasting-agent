"""Required Exa is a bounded provider obligation, never a quota or finish loop."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.search_policy import requirement
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.runtime.contracts import ContractError
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.providers.exa_search import ExaError
from ForecastAgent.evidence.acceptance import collection_acceptance
from ForecastAgent.tests.test_runtime_contracts import LIVE, SEARCH, prepared
from ForecastAgent.tests.test_collection import call


EXA = {'query':'Agency release March official record', 'need_ids':['n'],
       'reason':'Independent official release discovery', 'search_role':'crosscheck',
       'category':'general', 'include_domains':[]}
PAYLOAD = {'results':[], 'raw_response':{}, 'request_payload':{}, 'request_id':'required-test'}


class RequiredExaTests(TestCase):
    @patch.dict(os.environ, {'EXA_API_KEY':'test-only'})
    def test_normal_finish_is_unavailable_until_real_attempt(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            self.assertEqual(task.bundle['search_policy']['exa'], 'required')
            names = [t['function']['name'] for t in active_tools(task, COLLECTION_TOOLS)]
            self.assertNotIn('finish_collection', names)
            with self.assertRaises(ContractError) as error:
                task.execute('finish_collection', {'gaps':[]}, '')
            self.assertEqual(error.exception.details['code'], 'required_search_pending')
            self.assertEqual(len(task.bundle['exa_searches']), 0)

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.search_exa', return_value=deepcopy(PAYLOAD))
    @patch('ForecastAgent.runtime.retrieval.search_batch', return_value={'results':[]})
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_primary_then_required_exa_then_export_counts_exactly_one(self, model, tavily, exa):
        model.side_effect = [call('search_tavily', SEARCH, 'primary'),
                             call('search_exa', EXA, 'crosscheck'),
                             call('finish_collection', {'gaps':['No new hits']}, 'end')]
        with TemporaryDirectory() as root:
            task = prepared(root); task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertIsNone(model.call_args_list[0].kwargs['forced_tool'])
            self.assertEqual(model.call_args_list[1].kwargs['forced_tool'], 'search_exa')
            self.assertEqual(len(result['searches']), 1)
            self.assertEqual(len(result['exa_searches']), 1)
            self.assertTrue(result['result']['exa_requirement']['attempt_requirement_met'])
            self.assertEqual(result['result']['exa_requirement']['status'], 'attempted_completed')
            self.assertFalse(result['result']['acquisition_complete'])
            calls = model.call_count
            run_retrieval(LIVE, root, '', '')
            self.assertEqual(model.call_count, calls)
            tavily.assert_called_once(); exa.assert_called_once()

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.search_exa', return_value=deepcopy(PAYLOAD))
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_invalid_query_does_not_satisfy_obligation_or_spend_attempt(self, model, exa):
        model.side_effect = [call('search_exa', {**EXA, 'query':'dated_observations'}, 'invalid'),
                             call('search_exa', EXA, 'fixed'),
                             call('finish_collection', {'gaps':['No hits']}, 'end')]
        with TemporaryDirectory() as root:
            task = prepared(root); task.bundle['searches']=[{'status':'completed','results':[]}]; task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(result['transcript'][0]['result']['data']['contract_error']['code'], 'ungrounded_query')
            self.assertEqual(model.call_args_list[1].kwargs['forced_tool'], 'search_exa')
            self.assertEqual(len(result['exa_searches']), 1)
            exa.assert_called_once()

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.search_exa', side_effect=ExaError(402, 'Credits unavailable'))
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_provider_failure_counts_once_and_does_not_force_another_search(self, model, exa):
        model.side_effect = [call('search_exa', EXA, 'attempt'), call('finish_collection', {'gaps':['Provider failed']}, 'end')]
        with TemporaryDirectory() as root:
            task = prepared(root); task.bundle['searches']=[{'status':'completed','results':[]}]; task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(result['result']['exa_requirement']['status'], 'attempted_failed')
            self.assertTrue(result['result']['exa_requirement']['attempt_requirement_met'])
            self.assertEqual(result['exa_searches'][0]['http_status'], 402)
            self.assertIsNone(model.call_args_list[1].kwargs['forced_tool'])
            exa.assert_called_once()

    @patch.dict(os.environ, {'EXA_API_KEY':''})
    def test_missing_key_is_explicit_gap_and_later_key_never_grants_credit(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            output = task.execute('finish_collection', {'gaps':[]}, '')
            self.assertEqual(output['exa_requirement']['status'], 'unavailable')
            self.assertFalse(output['exa_requirement']['attempt_requirement_met'])
            self.assertTrue(any('Required Exa' in gap for gap in output['gaps']))
            acceptance = collection_acceptance(task.bundle)
            self.assertEqual(acceptance['status'], 'failed')
            self.assertFalse(acceptance['required_searches']['exa']['attempt_requirement_met'])
            with patch.dict(os.environ, {'EXA_API_KEY':'now-configured'}):
                restored = RetrievalTask(Path(root), LIVE)
                self.assertEqual(restored.exa_limit, 0)
                self.assertEqual(restored.bundle['search_policy']['exa'], 'required')

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    @patch('ForecastAgent.runtime.retrieval.search_exa')
    def test_three_invalid_queries_close_with_unmet_gap_without_http(self, exa, model):
        model.side_effect = [call('search_exa', {**EXA,'query':'dated_observations'}, str(i)) for i in range(3)]
        model.side_effect = list(model.side_effect)+[call('finish_collection', {'gaps':['Invalid query attempts']}, 'end')]
        with TemporaryDirectory() as root:
            task = prepared(root); task.bundle['searches']=[{'status':'completed','results':[]}]; task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(model.call_count, 4)
            self.assertEqual(model.call_args.kwargs['forced_tool'], 'finish_collection')
            self.assertEqual(len(result['exa_searches']), 0)
            self.assertFalse(result['result']['exa_requirement']['attempt_requirement_met'])
            self.assertFalse(result['result']['incomplete'] if 'incomplete' in result['result'] else False)
            self.assertTrue(any('Required Exa' in gap for gap in result['result']['gaps']))
            exa.assert_not_called()

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only'})
    def test_unknown_interrupted_reservation_is_consumed_and_reported(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            task.bundle['exa_searches']=[{'status':'reserved','results':[]}]
            output = task.execute('finish_collection', {'gaps':[]}, '')
            self.assertEqual(output['exa_requirement']['status'], 'interrupted_unknown')
            self.assertFalse(output['exa_requirement']['attempt_requirement_met'])
            self.assertEqual(task.budget()['exa_search_remaining'], 0)

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only'})
    def test_existing_optional_policy_and_allowance_are_preserved(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            task.bundle.pop('search_policy')
            task.bundle['acquisition_limits']['exa_search']=0
            task.save()
            restored = RetrievalTask(Path(root), LIVE)
            self.assertEqual(restored.bundle['search_policy']['exa'], 'optional')
            self.assertEqual(restored.exa_limit, 0)
            self.assertFalse(requirement(restored)['required'])

    @patch.dict(os.environ, {'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_required_exa_never_overrides_lifetime_model_limit(self, model):
        with TemporaryDirectory() as root:
            task = prepared(root); task.bundle['model_attempts']=[{'status':'reserved'}]*72; task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(result['session_state'], 'budget_exhausted')
            self.assertFalse(result['result']['exa_requirement']['attempt_requirement_met'])
            self.assertTrue(any('Required Exa' in gap for gap in result['result']['gaps']))
            model.assert_not_called()
