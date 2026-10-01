"""Regressions for failures observed in the new five-case pilot; no live calls."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch, MagicMock

from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.temporal_policy import amend_current
from ForecastAgent.runtime.task_protocol import task_view, supplemental_messages, validate_current_plan
from ForecastAgent.runtime.guidance import collection_system
from ForecastAgent.runtime.collection_actions import discovery_read_action
from ForecastAgent.runtime.telemetry import model_observer
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.runtime.contracts import validate, ContractError
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.providers.exa_search import options
from ForecastAgent.tests.test_collection import REQUEST, PLAN, URL, page, call

CURRENT = {**REQUEST, 'as_of_utc': '2026-07-09T00:00:00+00:00',
    'mode': 'historical_exploratory', 'acquisition_profile': 'collection_v3', 'exa_search_policy': 'optional'}
BANNER = """An official website of the United States government
Here's how you know
Official websites use .gov
A .gov website belongs to an official government organization in the United States.
Secure .gov websites use HTTPS
A lock ( ) or https:// means you've safely connected to the .gov website. Share sensitive information only on official, secure websites."""


class SystemProtocolTests(TestCase):
    def task(self, directory):
        task = RetrievalTask(Path(directory), CURRENT)
        amend_current(task.bundle, 'Current-information test')
        task.cutoff = None
        task.verified_only = False
        task.bundle['control']['operating_clock_utc'] = '2026-10-01T00:00:00+00:00'
        task.execute('plan_evidence', PLAN, '')
        return task

    def test_all_model_views_share_clock_and_prompts_are_model_neutral(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            view = task_view(task)
            self.assertNotIn('mode', view['question'])
            self.assertNotIn('as_of_utc', view['question'])
            self.assertEqual(view['operating_clock_utc'], '2026-10-01T00:00:00+00:00')
            self.assertEqual(view['provenance']['original_as_of_utc'], CURRENT['as_of_utc'])
            messages = supplemental_messages(task)
            self.assertEqual(json.loads(messages[1]['content'])['task_protocol'], view)
            self.assertNotIn('ultra', collection_system(task, {}).lower())
            self.assertNotIn('ultra', messages[0]['content'].lower())

    def test_past_month_forecast_is_not_a_critical_current_need(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            need = {'id': 'past', 'priority': 'critical', 'condition': 'Weather forecast for remainder of August 2026'}
            with self.assertRaisesRegex(ContractError, 'operating clock'):
                validate_current_plan(task, [need])
            need['condition'] = 'Observed maximum temperatures for August 2026'
            validate_current_plan(task, [need])
            need['condition'] = 'Archived historical forecast for August 2026'
            validate_current_plan(task, [need])

    def test_government_chrome_is_not_substantive_but_real_content_survives(self):
        self.assertFalse(body_diagnostics(BANNER)['usable_text'])
        self.assertEqual(body_diagnostics(BANNER)['state'], 'government_banner_only')
        self.assertTrue(body_diagnostics(BANNER + '\n' + 'Form 1 filing committee name and filing date. ' * 8)['usable_text'])

    def test_exa_bad_category_filter_is_rejected_before_reserving(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            with patch('ForecastAgent.runtime.retrieval.search_exa') as network:
                with self.assertRaisesRegex(ValueError, 'publication'):
                    task.execute('search_exa', {'query': 'Test publication', 'category': 'publication',
                        'include_domains': ['climate.copernicus.eu'], 'need_ids': ['n'],
                        'reason': 'Official record', 'search_role': 'gap'}, '')
                network.assert_not_called()
            self.assertEqual(task.bundle['exa_searches'], [])
        self.assertEqual(options('July climate bulletin', 'general', ['climate.copernicus.eu'])['category'], 'general')

    def test_discovery_requires_read_and_saved_reads_release_the_gate(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            task.bundle['searches'].append({'status': 'completed', 'results': [{'url': URL, 'title': 'Primary release'}]})
            self.assertEqual(discovery_read_action(task)['tool'], 'read_sources')
            self.assertNotIn('search_tavily', [t['function']['name'] for t in active_tools(task, COLLECTION_TOOLS)])
            with self.assertRaisesRegex(ContractError, 'before another search'):
                validate(task, 'search_tavily', {'query': 'Test release', 'need_ids': ['n'], 'reason': 'Repeat', 'search_role': 'gap',
                    'topic': 'general', 'include_domains': [], 'include_domains_mode': 'prefer', 'exact_match': False}, COLLECTION_TOOLS)
            with patch('ForecastAgent.runtime.retrieval.fetch_public_page', return_value=page()):
                task.execute('read_sources', {'urls': [URL], 'queries': [{'query': 'publication', 'need_ids': ['n']}]}, '')
            self.assertIsNone(discovery_read_action(task))
            self.assertEqual(len(task.bundle['searches']), 1)

    def test_transport_failures_have_their_own_nonrenewable_allowance(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            observe = model_observer(task, dispatch_limit=16, failure_limit=4)
            for i in range(4):
                record = {'started_at_utc': '2026-10-01T00:00:00Z', 'retry_index': i, 'status': 'reserved'}
                token = observe('reserve', record)
                observe('complete', {**record, 'status': 'missing_choices'}, token)
            with self.assertRaisesRegex(RuntimeError, 'failure allowance'):
                observe('reserve', record)
            self.assertEqual(len(task.bundle['model_attempts']), 4)
            self.assertEqual(task.bundle['searches'], [])

    def test_supported_station_parameters_preserve_identity_and_discovery_guard(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            base = 'https://mesonet.agron.iastate.edu/sites/hist.phtml?station=DEVC1&network=CA_DCP'
            task.bundle['source_leads'][base] = {'url': base, 'origin': 'question_resolution_criteria'}
            result = task.execute('parameterize_source', {'url': base, 'year': 2026, 'month': 8}, '')
            self.assertIn(result['url'], task.catalog())
            self.assertIn('station=DEVC1', result['url'])
            self.assertIn('network=CA_DCP', result['url'])
            self.assertEqual(len(task.bundle['fetch_attempts']), 0)
            with self.assertRaises(ValueError):
                task.execute('parameterize_source', {'url': base.replace('DEVC1', 'OTHER'), 'year': 2026, 'month': 8}, '')

    def test_model_can_change_without_changing_prompts_or_tools(self):
        from ForecastAgent.providers.model import ask_model
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"tool_calls":[]}}]}'
        with patch.dict(os.environ, {'FORECAST_MODEL': 'example/tool-model'}), patch('ForecastAgent.providers.ultra.urlopen', return_value=response) as network:
            ask_model([{'role': 'user', 'content': 'Collect information'}], 'test', tools=[])
            payload = json.loads(network.call_args.args[0].data)
            self.assertEqual(payload['model'], 'example/tool-model')
            self.assertEqual(payload['messages'][0]['content'], 'Collect information')

    def test_batch_read_rescues_banner_once_and_keeps_raw_failure(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            task.bundle['plan'][0]['expected_source'] = 'example.org official publication'
            blocked = {**page(), 'content': BANNER}
            with patch('ForecastAgent.runtime.retrieval.cached_page', return_value=None), \
                 patch('ForecastAgent.runtime.retrieval.fetch_public_page', return_value=blocked) as fetch, \
                 patch('ForecastAgent.runtime.retrieval.extract_basic', return_value={'results': [{'url': URL, 'raw_content': page()['content']}]}) as extract:
                result = task.execute('read_sources', {'urls': [URL], 'queries': [{'query': 'publication', 'need_ids': ['n']}]}, 'test')
                self.assertTrue(result['reads'][0]['ok'])
                self.assertTrue(result['located_material'][0]['passages'])
                self.assertEqual(len(task.bundle['failed_captures']), 1)
                self.assertEqual(task.bundle['failed_captures'][0]['page']['content'], BANNER)
                task.execute('read_sources', {'urls': [URL], 'queries': [{'query': 'publication', 'need_ids': ['n']}]}, 'test')
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(extract.call_count, 1)
                self.assertEqual(len(task.bundle['extract_attempts']), 1)

    def test_interruption_export_retains_completed_provider_obligation(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            task.bundle['search_policy']['exa'] = 'required'
            task.bundle['exa_searches'] = [{'status': 'completed', 'results': []}]
            task.save()
            with patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=RuntimeError('Provider overloaded 503')):
                output = run_retrieval(CURRENT, directory, '', '')
            self.assertTrue(output['result']['incomplete'])
            self.assertTrue(output['result']['exa_requirement']['attempt_requirement_met'])
            exported = json.loads((Path(directory) / 'intelligence.json').read_text(encoding='utf-8'))
            self.assertEqual(len(output['exa_searches']), 1)
            self.assertFalse(output['result']['acquisition_complete'])
