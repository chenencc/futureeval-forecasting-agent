"""Offline regressions for source binding, forced closure and stage recovery."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition.pipeline import prepare, run, verify_baseline
from ForecastAgent.runtime.intelligent_acquisition import STRATEGY, assess, frontier, configure_tools
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.collection_actions import next_action
from ForecastAgent.runtime.contracts import ContractError
from ForecastAgent.runtime.context import collection_context
from ForecastAgent.runtime.progress import snapshot, delta
from ForecastAgent.readers.saved import version_digest
from ForecastAgent.tools.registry import COLLECTION_TOOLS

URL = 'https://example.org/dataset'
REQUEST = {'id': '999001', 'question': 'Will the agency publish the June statistic?',
           'resolution_criteria': 'Use the agency June table, including units and observation date.',
           'background': 'The official table is at https://example.org/dataset.',
           'mode': 'live', 'question_type': 'binary', 'open_time': '2026-06-01T00:00:00Z'}
PLAN = {'needs': [{'id': 'series', 'condition': 'June statistic with units and date',
                  'priority': 'critical', 'expected_source': 'agency June table',
                  'query': 'agency June statistic', 'question_spans': [
                      {'field': 'resolution_criteria', 'quote': 'agency June table'}]}],
        'entity_card': {'subject': 'agency statistic', 'identity_checks': 'Exact agency series and metric',
                        'required_form': 'Official dataset table', 'announcement_window': 'June observation period',
                        'effective_vs_announcement': 'Observation date is distinct from publication date'}}
TEXT = 'Agency official dataset: complete June observations.\nDate | Metric | Units\n2026-06-30 | 184368461962.97 | USD\nThe June row is an observed value, distinct from the publication date.\n' * 2


def source(text=TEXT):
    raw = text.encode()
    return {'url': URL, 'content': text, 'raw_response_base64': base64.b64encode(raw).decode(),
            'sha256': hashlib.sha256(raw).hexdigest(), 'declared_content_type': 'text/plain',
            'retrieved_at_utc': '2026-07-01T00:00:00Z', 'temporal_status': 'live_capture'}


def task_at(directory):
    request, _ = prepare(REQUEST)
    task = RetrievalTask(Path(directory), request)
    task.execute('plan_evidence', copy.deepcopy(PLAN), '')
    return task


def bank(task):
    task.bundle['pages'][URL] = source()
    return task.execute('record_quote', {'url': URL,
        'quote': 'Date | Metric | Units\n2026-06-30 | 184368461962.97 | USD',
        'occurrence_index': 1, 'need_ids': ['series']}, '')['excerpt']['id']


def declaration(xid, status='adequate'):
    return {'items': [{'need_id': 'series', 'status': status, 'source_urls': [URL],
                       'excerpt_ids': [xid], 'reason': 'The exact June row and its date and units were banked.',
                       'missing_material': '' if status == 'adequate' else 'The independent source remains unavailable.',
                       'next_action': ''}]}


def call(name, args, ident):
    return {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


class IntelligentAcquisitionTests(TestCase):
    def test_release_analysis_and_transport_dependencies_remain_frozen(self):
        self.assertEqual(verify_baseline()['baseline_release'], 'v1.0.1')

    def test_prepare_preserves_metadata_and_rejects_nested_outcomes(self):
        request, warnings = prepare(REQUEST)
        self.assertEqual(request['open_time'], REQUEST['open_time'])
        self.assertEqual(request['resolution_criteria'], REQUEST['resolution_criteria'])
        self.assertEqual(request['acquisition_strategy'], STRATEGY)
        self.assertFalse(warnings)
        with self.assertRaisesRegex(ValueError, 'Outcome'):
            prepare({**REQUEST, 'extra': {'resolved_to': 'yes'}})
        for ident in ('../escape', '', 'a/b'):
            with self.assertRaises(ValueError):
                prepare({**REQUEST, 'id': ident})

    def test_invalid_rule_binding_cannot_freeze_plan_or_spend_budget(self):
        with TemporaryDirectory() as directory:
            request, _ = prepare(REQUEST)
            task = RetrievalTask(Path(directory), request)
            plan = copy.deepcopy(PLAN)
            plan['needs'][0]['question_spans'][0]['quote'] = 'Invented official acknowledgement requirement'
            budget = task.budget()
            with self.assertRaises(ContractError):
                task.execute('plan_evidence', plan, '')
            self.assertIsNone(task.bundle['plan'])
            self.assertEqual(task.budget(), budget)

    def test_assessment_requires_real_exact_material_and_is_not_progress(self):
        with TemporaryDirectory() as directory:
            task = task_at(directory)
            task.bundle['pages'][URL] = source()
            with self.assertRaises(ContractError):
                assess(task, declaration('invented'))
            xid = bank(task)
            before = snapshot(task)
            budget = task.budget()
            result = assess(task, declaration(xid))
            self.assertFalse(result['semantic_verified'])
            self.assertFalse(delta(before, snapshot(task))['advanced'])
            self.assertEqual(budget, task.budget())
            self.assertEqual(assess(task, declaration(xid))['changed_need_ids'], [])
            self.assertEqual(len(task.bundle['material_assessment_events']), 1)
            task.bundle['pages'][URL] = source(TEXT+' Revised June row.')
            self.assertTrue(frontier(task)['needs'][0]['assessment_stale'])
            with self.assertRaises(ContractError):
                assess(task, declaration(xid))

    def test_assessment_batch_is_atomic_and_critical_requirement_cannot_be_retired(self):
        with TemporaryDirectory() as directory:
            task = task_at(directory)
            xid = bank(task)
            args = declaration(xid)
            args['items'].append({**args['items'][0], 'need_id': 'missing'})
            with self.assertRaises(ContractError):
                assess(task, args)
            self.assertFalse(task.bundle.get('material_assessments'))
            with self.assertRaises(ContractError):
                task.execute('set_acquisition_need_status', {'need_id': 'series', 'status': 'not_applicable',
                    'reason': 'The document could not be retrieved by the search tools.'}, '')

    def test_frontier_paginates_failed_sources_and_never_invents_leads(self):
        with TemporaryDirectory() as directory:
            task = task_at(directory)
            task.bundle['source_leads'] = {f'https://example.org/{i}': {'origin': 'page_link'} for i in range(24)}
            task.bundle['fetch_attempts'] = [{'url': 'https://example.org/0', 'status': 'failed'}]
            view = frontier(task, limit=5)
            self.assertEqual(view['source_total'], 24)
            self.assertEqual(view['next_offset'], 5)
            self.assertEqual(view['sources'][0]['state'], 'attempted')
            self.assertEqual(len(frontier(task, offset=20, limit=5)['sources']), 4)

    def test_unpublished_material_is_preserved_as_gap_without_event_verdict(self):
        with TemporaryDirectory() as directory:
            task = task_at(directory)
            args = declaration('unused', status='not_yet_published')
            args['items'][0].update(source_urls=[], excerpt_ids=[],
                missing_material='The final June publication is not available at the operating date.',
                next_action='Read available historical observations within the existing budget.')
            assess(task, args)
            task.bundle['control']['forced_close'] = True
            result = task.execute('finish_collection', {'gaps': ['Final publication unavailable.']}, '')
            self.assertEqual(result['material_report']['unresolved_material_targets'][0]['status'], 'not_yet_published')
            self.assertFalse(result['truth_verified'])
            self.assertFalse(result['acquisition_complete'])

    def test_empty_application_shell_cannot_support_adequate_material(self):
        with TemporaryDirectory() as directory:
            task = task_at(directory)
            task.bundle['pages'][URL] = source('Please enable JavaScript to continue.')
            with self.assertRaises(ValueError):
                task.execute('record_quote', {'url': URL, 'quote': 'Please enable JavaScript', 'need_ids': ['series']}, '')
            with self.assertRaises(ContractError):
                assess(task, declaration('missing'))

    def test_forced_close_exports_unreviewed_targets_and_preserves_search_reservations(self):
        with TemporaryDirectory() as directory:
            task = task_at(directory)
            task.bundle['searches'] = [{'results': [], 'status': 'reserved'}]*3
            task.bundle['control']['forced_close'] = True
            result = task.execute('finish_collection', {'gaps': ['Forced closure with missing original material.']}, '')
            self.assertFalse(result['acquisition_complete'])
            self.assertEqual(result['material_report']['unresolved_material_targets'][0]['status'], 'unreviewed')
            restored = RetrievalTask(Path(directory), task.bundle['request'])
            self.assertEqual(restored.budget()['tavily_basic_remaining'], 0)

    def test_context_keeps_material_policy_and_baseline_tools_are_not_extended(self):
        with TemporaryDirectory() as directory:
            task = task_at(directory)
            task.bundle['messages'] = [{'role': 'system', 'content': 'Collect original material.'}]
            view = collection_context(task)
            content = json.loads(view[1]['content'])
            self.assertIn('material_frontier', content)
            self.assertNotIn('Do not require comprehension, excerpts', content['instruction'])
            self.assertIsNone(next_action(task))
            baseline = RetrievalTask(Path(directory)/'baseline', {**REQUEST, 'pipeline': 'collection', 'acquisition_profile': 'collection_v3'})
            names = [t['function']['name'] for t in configure_tools(baseline, COLLECTION_TOOLS)]
            self.assertNotIn('assess_materials', names)

    def test_actual_loop_banks_complete_table_and_closes_without_extra_search(self):
        with TemporaryDirectory() as directory, patch.dict('os.environ', {'EXA_API_KEY': ''}):
            request, _ = prepare(REQUEST)
            search_args = {'query': '"agency" June statistic', 'need_ids': ['series'], 'reason': 'Find the exact agency June table',
                           'topic': 'general', 'include_domains': [], 'include_domains_mode': 'prefer', 'exact_match': True,
                           'search_role': 'primary'}
            quote = 'Date | Metric | Units\n2026-06-30 | 184368461962.97 | USD'
            messages = [
                {'tool_calls': [call('plan_evidence', PLAN, 'p')]},
                {'tool_calls': [call('search_tavily', search_args, 's')]},
                {'tool_calls': [call('read_sources', {'urls': [URL], 'rescue_failed': False,
                    'queries': [{'query': 'June observations USD', 'need_ids': ['series']}]}, 'r')]},
                {'tool_calls': [call('record_quote', {'url': URL, 'quote': quote, 'occurrence_index': 1, 'need_ids': ['series']}, 'q'),
                                call('assess_materials', declaration('X1'), 'a'), call('finish_collection', {'gaps': []}, 'f')]},
            ]
            with patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=messages) as model, \
                 patch('ForecastAgent.runtime.retrieval.search_batch', return_value={'results': [{'url': URL, 'title': 'Agency June table'}]}) as search, \
                 patch('ForecastAgent.runtime.retrieval.fetch_public_page', return_value=source()):
                bundle = run_retrieval(request, directory, 'test-search-key', 'test-router-key')
            self.assertEqual(model.call_count, 4, json.dumps(bundle.get('transcript', []), default=str)[-7000:])
            self.assertEqual(search.call_count, 1, json.dumps(bundle.get('transcript', []), default=str)[-6000:])
            self.assertEqual(bundle['excerpts'][0]['text'], quote)
            self.assertFalse(bundle['result']['material_report']['unresolved_material_targets'])
            self.assertFalse(bundle['submitted_to_metaculus'])
            with patch('ForecastAgent.runtime.retrieval.ask_ultra') as second_model:
                run_retrieval(request, directory, '', '')
                second_model.assert_not_called()

    def test_pipeline_reuses_completed_package_and_detects_tampering(self):
        with TemporaryDirectory() as directory, patch.dict('os.environ', {'EXA_API_KEY': ''}):
            def collector(request, task_dir):
                task = RetrievalTask(task_dir, request)
                task.execute('plan_evidence', copy.deepcopy(PLAN), '')
                xid = bank(task)
                assess(task, declaration(xid))
                task.execute('finish_collection', {'gaps': []}, '')
                return task.bundle
            with patch('ForecastAgent.agent.run_research', side_effect=collector) as collect:
                report = run(REQUEST, directory)
                self.assertEqual(report['state'], 'complete')
                self.assertFalse(report['analysis_run'])
                self.assertFalse(report['submitted'])
                self.assertEqual(run(REQUEST, directory), report)
                self.assertEqual(collect.call_count, 1)
                with self.assertRaisesRegex(ValueError, 'silent restart'):
                    run({**REQUEST, 'background': 'Different input.'}, directory)
                (Path(directory)/'package.json').write_text('{}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    run(REQUEST, directory)

    def test_supplement_failure_resumes_the_exact_parent_without_recollection(self):
        with TemporaryDirectory() as directory, patch.dict('os.environ', {'EXA_API_KEY': ''}):
            def collector(request, task_dir):
                task = RetrievalTask(task_dir, request)
                task.execute('plan_evidence', copy.deepcopy(PLAN), '')
                bank(task)
                task.execute('finish_collection', {'gaps': ['Independent material unavailable.']}, '')
                return task.bundle
            with patch('ForecastAgent.agent.run_research', side_effect=collector) as collect:
                with patch('ForecastAgent.supplement.stage.run', side_effect=RuntimeError('Interrupted supplement')):
                    with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
                        run(REQUEST, directory)
                raw = (Path(directory)/'collection/bundle.json').read_bytes()
                self.assertEqual(json.loads((Path(directory)/'state.json').read_text())['stage'], 'supplement')
                report = run(REQUEST, directory)
                self.assertEqual(collect.call_count, 1)
                self.assertEqual((Path(directory)/'collection/bundle.json').read_bytes(), raw)
                self.assertTrue(report['material_report']['unresolved_material_targets'])
