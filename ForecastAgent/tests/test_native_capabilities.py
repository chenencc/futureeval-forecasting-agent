"""Integration gates: shared quotas, exact views, recovery and map feedback."""
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.channels import native
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.tools import capabilities
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.tools.intelligence_box import core
from ForecastAgent.research_loop import POLICY, fusion, state

URL = 'https://www.microsoft.com/en-us/Investor/earnings/release'
RAW = b'''<html><body><h1>Issuer Q3 revenue release</h1>
<p>The official issuer release reports quarterly revenue in USD millions.</p>
<table><tr><th>Period</th><th>Revenue USD million</th></tr>
<tr><td>2026 Q2</td><td>110</td></tr><tr><td>2026 Q3</td><td>125</td></tr></table>
<h2>Outlook</h2><p>The next quarterly release is scheduled after this observation period.</p></body></html>'''
BODY = 'The saved original release contains issuer financial statements. Exact quarterly revenue requires the table.'
STAMP = '2026-10-10T00:00:00+00:00'
FETCH = {'source_id': 'worldbank', 'parameters': {'country': 'US', 'indicator': 'NY.GDP.MKTP.CD', 'date': '2025'}, 'need_ids': ['revenue']}


def extension_read(task, name, args):
    return {'text': task.bundle['pages'][args['url']]['content'], 'network_requests': 0}


def task(root, *, graph=False, enabled=True, mode='live'):
    request = {'question': 'Will issuer revenue exceed USD 120 million?',
               'resolution_criteria': 'Use the issuer Q3 revenue release. ' + URL,
               'mode': mode, 'pipeline': 'collection'}
    if enabled:
        request[native.FIELD] = native.POLICY
    if graph:
        request.update(research_state_policy=POLICY, research_acquisition_policy=fusion.POLICY_NAME)
    if mode != 'live':
        request['as_of_utc'] = '2026-10-11T00:00:00+00:00'
    t = RetrievalTask(Path(root), request)
    t.bundle['plan'] = [{'id': 'revenue', 'priority': 'critical', 'condition': 'Issuer Q3 revenue in USD millions',
                         'expected_source': 'Issuer release', 'query': 'Q3 revenue'}]
    t.store_page(URL, {'content': BODY, 'raw_response_base64': base64.b64encode(RAW).decode(),
                      'sha256': hashlib.sha256(RAW).hexdigest(), 'content_type': 'text/html',
                      'retrieved_at_utc': STAMP, 'temporal_status': 'live_capture', 'http_status': 200})
    if graph:
        fusion.initialize(t)
    t.save()
    return t


def response(url, records=None, *, status=200, truncated=False):
    payload = [{'total': 1, 'pages': 1, 'page': 1}, records if records is not None else [
        {'date': '2025', 'value': 125, 'unit': 'USD', 'country': {'id': 'US'}}]]
    return {'raw': json.dumps(payload).encode(), 'status': status, 'final_url': url,
            'content_type': 'application/json', 'response_headers': {}, 'truncated': truncated}


class NativeCapabilityTests(unittest.TestCase):
    def test_final_map_review_retains_configured_collection_model_and_restores_environment(self):
        import os
        import time
        from ForecastAgent.intelligence import pipeline, development_collection
        from ForecastAgent.research_loop import post_supplement
        with tempfile.TemporaryDirectory() as root:
            request = {'id': '123', native.FIELD: native.POLICY}
            package = {'request': request}
            reservation = {'limits': {'seconds_remaining': 180}, 'started_at_epoch': time.time()}
            def read(path):
                if path.name == 'map-reservation.json':
                    return reservation
                return {'post_reservation_sha256': pipeline.digest(reservation)}
            def review(value, *args, **kwargs):
                self.assertEqual(os.environ['FORECAST_MODEL'], development_collection.DEFAULT_MODEL)
                self.assertEqual(os.environ['FORECAST_MODEL_FALLBACK_SUPER'], '0')
                self.assertEqual(kwargs['http_cap'], 2)
                return value, {'status': 'map_review_complete','usage':{'totals':{'http_attempts':0}},
                    'receipt_sha256':{},'review_bundle_sha256':pipeline.digest(value),
                    'original_pages_preserved':True,'historical_ledgers_preserved':True}
            with patch.dict(os.environ, {'FORECAST_MODEL': 'previous-model'}), \
                 patch.object(pipeline, 'contract', return_value={}), \
                 patch.object(pipeline, 'load', side_effect=read), \
                 patch.object(development_collection, 'collect', return_value={'status': 'complete', 'package': package}), \
                 patch.object(post_supplement, 'enabled', return_value=True), \
                 patch.object(post_supplement, 'remaining_http', return_value=2), \
                 patch.object(post_supplement, 'remaining_failures', return_value=4), \
                 patch.object(post_supplement, 'run', side_effect=review), \
                 patch.object(pipeline, 'prepare_package', return_value=({}, {'status': 'ready'})):
                result = pipeline.collect(request, Path(root), clock_utc=STAMP,
                    recover_data=False, channel_tools=True)
                self.assertEqual(os.environ['FORECAST_MODEL'], 'previous-model')
            self.assertEqual(result['report']['post_supplement_map']['status'], 'map_review_complete')

    def test_channel_composition_uses_candidate_adapter_not_frozen_release(self):
        from ForecastAgent.intelligence import pipeline
        from ForecastAgent.intelligence import development_collection
        from ForecastAgent.releases import v1_0_5
        with tempfile.TemporaryDirectory() as root:
            request = {'id': '123', native.FIELD: native.POLICY}
            package = {'request': request}
            with patch.object(pipeline, 'contract', return_value={}), \
                 patch.object(v1_0_5, 'collect', side_effect=AssertionError('Frozen release is not the candidate')), \
                 patch.object(development_collection, 'collect', return_value={'status': 'complete', 'package': package}) as collect, \
                 patch.object(pipeline, 'prepare_package', return_value=({'pages': {}}, {'status': 'ready'})):
                result = pipeline.collect(request, Path(root), clock_utc=STAMP,
                    recover_data=False, channel_tools=True)
            self.assertEqual(result['view'], {'pages': {}})
            self.assertFalse(result['analysis_started'])
            self.assertFalse(result['submitted'])
            self.assertEqual(collect.call_args.args[1], Path(root) / 'retrieval/development-channels')

    def test_local_auxiliary_reads_reject_another_task_capture_before_open(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core.Toolbox, 'call', side_effect=AssertionError('Must reject before toolbox access')):
                for name in ('intelligence_links', 'intelligence_tables', 'intelligence_provisions'):
                    with self.assertRaisesRegex(ValueError, 'already recorded by this task'):
                        t.execute(name, {'capture_id': 'another-task-capture'}, '')
            self.assertEqual(t.bundle['fetch_attempts'], [])

    def test_agent_envelope_keeps_channel_status_and_provenance(self):
        from ForecastAgent.tools.channels import tool_result
        result = tool_result('intelligence_fetch', {'status': 'configuration_required'}, {})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'configuration_required')
        self.assertEqual(result['provenance']['channel_ids'], ['native_official_channels'])
        self.assertFalse(result['provenance']['truth_verified'])
        empty = tool_result('intelligence_fetch', {'status': 'empty'}, {})
        self.assertEqual(empty['status'], 'empty')
        self.assertEqual(tool_result('intelligence_part', {}, {})['provenance']['channel_ids'], ['original_navigation'])

    def test_registered_reader_executes_without_adding_a_runtime_name_branch(self):
        from ForecastAgent.tools.registry import tool
        c = capabilities.Capability('fixture_reader', tool('fixture_reader', 'Read a fixture',
            {'url': {'type': 'string'}}, ['url']), kind='read', effects=('saved_read',),
            handler='ForecastAgent.tests.test_native_capabilities:extension_read', policy=native.POLICY)
        capabilities.register(c)
        try:
            with tempfile.TemporaryDirectory() as root:
                t = task(root)
                self.assertEqual(t.execute('fixture_reader', {'url': URL}, '')['text'], BODY)
                with self.assertRaisesRegex(ValueError, 'Duplicate'):
                    capabilities.register(c)
                with self.assertRaises(ValueError):
                    t.execute('fixture_reader', {}, '')
                self.assertEqual(t.bundle['fetch_attempts'], [])
        finally:
            capabilities.registry().pop('fixture_reader')

    def test_external_supplement_material_is_observed_without_request_or_model(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, graph=True)
            url = 'https://example.org/official-supplement'
            t.bundle['pages'][url] = {'content': 'A new official supplement records revenue of 125 million USD for 2026 Q3.',
                'sha256': 'saved-original-hash', 'retrieved_at_utc': STAMP, 'capture_method': 'independent_supplement'}
            t.save()
            restored = RetrievalTask(Path(root), t.bundle['request'])
            arrivals = restored.bundle['channel_tools']['material_events']['events']
            self.assertEqual(arrivals[-1]['url'], url)
            self.assertEqual(arrivals[-1]['origin'], 'restored_or_external_stage')
            self.assertTrue(restored.bundle['research_acquisition']['pending_map_update'])
            self.assertEqual(restored.bundle['fetch_attempts'], [])

    def test_material_event_cache_tampering_blocks_resume(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            t.bundle['channel_tools']['material_events']['seen'][URL] = 'changed'; t.save()
            with self.assertRaisesRegex(ValueError, 'cache differs'):
                RetrievalTask(Path(root), t.bundle['request'])

    def test_development_collector_is_explicit_and_keeps_incomplete_state(self):
        from ForecastAgent.intelligence import development_collection
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            request = {**t.bundle['request'], 'id': '12345'}
            def interrupted(request, directory, **kwargs):
                import os
                self.assertEqual(os.environ['FORECAST_MODEL'], development_collection.DEFAULT_MODEL)
                self.assertEqual(os.environ['FORECAST_MODEL_FALLBACK_SUPER'], '0')
                return {'state': 'collection_incomplete', 'resumable': True}
            with patch.object(development_collection.pipeline, 'run', side_effect=interrupted), patch.dict('os.environ', {'FORECAST_MODEL': 'previous-model'}):
                result = development_collection.collect(request, Path(root) / 'development')
                import os
                self.assertEqual(os.environ['FORECAST_MODEL'], 'previous-model')
            self.assertTrue(result['resumable'])
            self.assertIsNone(result['release_version_claimed'])
            self.assertFalse(result['budget_reset'])

    def test_development_collector_composes_original_pipeline_and_checks_question(self):
        from ForecastAgent.intelligence import development_collection
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            request = {**t.bundle['request'], 'id': '12345', 'collection_model': 'configured-model'}
            def complete(request, directory, **kwargs):
                directory.mkdir(parents=True, exist_ok=True)
                (directory / 'package.json').write_text(json.dumps({'request': request, 'pages': {}}))
                return {'state': 'complete'}
            with patch.object(development_collection.pipeline, 'run', side_effect=complete) as runner:
                result = development_collection.collect(request, Path(root) / 'development')
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['package']['request']['collection_model'], 'configured-model')
            self.assertEqual(runner.call_count, 1)
            self.assertTrue(runner.call_args.kwargs['supplement_network'])

    def test_every_legacy_definition_is_registered_without_schema_mutation(self):
        before = copy.deepcopy(COLLECTION_TOOLS)
        for d in COLLECTION_TOOLS:
            self.assertIn(d['function']['name'], capabilities.registry())
        self.assertEqual(before, COLLECTION_TOOLS)

    def test_opt_in_exposes_exact_fourteen_tools_only_once(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            configured = capabilities.configure(t, copy.deepcopy(COLLECTION_TOOLS))
            names = [d['function']['name'] for d in configured]
            self.assertEqual(len([n for n in names if n.startswith('intelligence_')]), 14)
            self.assertEqual(len(names), len(set(names)))

    def test_old_task_retains_old_catalog_and_blocks_channel_execution(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, enabled=False)
            self.assertNotIn('channel_tools', t.bundle)
            self.assertEqual(capabilities.configure(t, COLLECTION_TOOLS), COLLECTION_TOOLS)
            with self.assertRaisesRegex(ValueError, 'not enabled'):
                t.execute('intelligence_catalog', {}, '')

    def test_unknown_capability_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with self.assertRaisesRegex(ValueError, 'Unregistered'):
                t.execute('unknown_fetch', {}, '')
            self.assertEqual(t.bundle['fetch_attempts'], [])

    def test_catalog_and_effects_share_the_registry(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            catalog = t.execute('list_channels', {}, '')
            ids = {c['id'] for c in catalog['capability_catalog']['capabilities']}
            self.assertIn('intelligence_part', ids)
            self.assertNotIn('network', capabilities.get('intelligence_part').effects)
            self.assertTrue(capabilities.produces_material('intelligence_part'))
            self.assertIn('intelligence_fetch', capabilities.network_tools())

    @patch.object(core, 'transport', side_effect=lambda u, *a, **k: response(u))
    def test_official_capture_spends_one_native_slot_and_restores_without_http(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            result = t.execute('intelligence_fetch', FETCH, '')
            self.assertEqual(result['status'], 'usable')
            self.assertEqual(t.budget()['page_fetch_remaining'], 7)
            self.assertEqual(len(t.bundle['fetch_attempts']), 1)
            self.assertEqual(result['budget_authority'], 'native_fetch_attempts')
            resumed = RetrievalTask(Path(root), t.bundle['request'])
            replay = resumed.execute('intelligence_fetch', FETCH, '')
            self.assertTrue(replay['cached'])
            self.assertEqual(wire.call_count, 1)
            self.assertEqual(len(resumed.bundle['fetch_attempts']), 1)

    @patch.object(core, 'transport', side_effect=lambda u, *a, **k: response(u))
    def test_existing_native_attempts_reduce_api_allowance(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            t.bundle['fetch_attempts'] = [{'status': 'failed'} for _ in range(7)]
            t.execute('intelligence_fetch', FETCH, '')
            failed = t.execute('intelligence_fetch', {**FETCH, 'parameters': {**FETCH['parameters'], 'date': '2024'}}, '')
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(len(t.bundle['fetch_attempts']), 8)
            self.assertEqual(wire.call_count, 1)

    @patch.object(core, 'transport', side_effect=lambda u, *a, **k: response(u, status=503))
    def test_failed_http_is_preserved_and_not_retried(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            result = t.execute('intelligence_fetch', FETCH, '')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(t.bundle['fetch_attempts'][0]['status'], 'failed')
            self.assertIn(result['id'], t.bundle['channel_raw_captures'])
            self.assertEqual(t.execute('intelligence_fetch', FETCH, '')['status'], 'failed')
            self.assertEqual(wire.call_count, 1)

    @patch.object(core, 'transport', side_effect=lambda u, *a, **k: response(u, []))
    def test_empty_api_response_is_not_readable_or_event_absence(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            result = t.execute('intelligence_fetch', FETCH, '')
            self.assertEqual(result['status'], 'empty')
            self.assertNotIn(result['request_url'], t.bundle['pages'])
            self.assertTrue(any('not proof of event absence' in s for s in result['limitations']))

    @patch.object(core, 'transport')
    @patch.dict('os.environ', {'CONGRESS_API_KEY': ''})
    def test_missing_credentials_never_reserve_http_and_can_be_rechecked(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, graph=True)
            args = {'source_id': 'congress_bill', 'parameters': {'congress': 119, 'bill_type': 'hr', 'bill_number': 1}, 'need_ids': ['revenue']}
            for _ in range(2):
                self.assertEqual(t.execute('intelligence_fetch', args, '')['status'], 'configuration_required')
            self.assertEqual(t.bundle['fetch_attempts'], [])
            wire.assert_not_called()

    @patch.object(core, 'transport')
    def test_guessed_detail_or_unobserved_cik_never_reaches_transport(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with self.assertRaisesRegex(ValueError, 'already discovered'):
                t.execute('intelligence_read', {'url': 'https://www.sec.gov/unknown', 'need_ids': ['revenue']}, '')
            with self.assertRaisesRegex(ValueError, 'already observed'):
                t.execute('intelligence_fetch', {'source_id': 'sec_submissions', 'parameters': {'cik': '0001318605'}, 'need_ids': ['revenue']}, '')
            wire.assert_not_called()
            self.assertEqual(t.bundle['fetch_attempts'], [])

    @patch.object(core, 'transport')
    def test_historical_api_is_blocked_but_eligible_saved_original_can_be_read(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, mode='historical_exploratory')
            with self.assertRaisesRegex(ValueError, 'live mode'):
                t.execute('intelligence_fetch', FETCH, '')
            t.bundle['pages'][URL]['temporal_status'] = 'local_pre_cutoff_capture'
            self.assertEqual(t.execute('intelligence_outline', {'url': URL}, '')['status'], 'indexed')
            wire.assert_not_called()

    @patch.object(core, 'transport', side_effect=lambda u, *a, **k: response(u))
    def test_completed_capture_recovers_crash_before_bundle_projection(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            result = t.execute('intelligence_fetch', FETCH, '')
            op = t.bundle['channel_tools']['operations'][0]
            op.pop('capture_id'); op['status'] = 'reserved'
            t.bundle['pages'].pop(result['request_url'], None)
            t.bundle['channel_tools']['captures'].clear(); t.save()
            resumed = RetrievalTask(Path(root), t.bundle['request'])
            recovered = resumed.execute('intelligence_fetch', FETCH, '')
            self.assertTrue(recovered['cached'])
            self.assertTrue(resumed.bundle['channel_tools']['operations'][0]['recovered_without_http'])
            self.assertEqual(wire.call_count, 1)
            self.assertIn(result['request_url'], resumed.bundle['pages'])

    @patch.object(core, 'transport', side_effect=lambda u, *a, **k: response(u))
    def test_graph_capture_and_cached_replay_use_same_feedback_and_quota(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, graph=True)
            result = t.execute('intelligence_fetch', FETCH, '')
            event = t.bundle['research_acquisition']['events'][-1]
            self.assertTrue(event['new_bodies'])
            self.assertTrue(t.bundle['research_acquisition']['pending_map_update'])
            self.assertEqual(event['budget_before']['page_fetch_remaining'] - event['budget_after']['page_fetch_remaining'], 1)
            t.execute('intelligence_fetch', FETCH, '')
            self.assertEqual(wire.call_count, 1)
            self.assertEqual(t.bundle['research_acquisition']['events'][-1]['new_bodies'], [])

    @patch.object(core, 'transport')
    def test_outline_search_part_are_offline_and_bind_exact_native_coordinates(self, wire):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, graph=True)
            before = copy.deepcopy(t.bundle['pages'][URL])
            outline = t.execute('intelligence_outline', {'url': URL}, '')
            self.assertIn('table:1', [u['unit_id'] for u in outline['units']])
            self.assertEqual(t.execute('intelligence_search', {'url': URL, 'query': '125'}, '')['hits'][0]['unit_id'], 'table:1')
            part = t.execute('intelligence_part', {'url': URL, 'unit_id': 'table:1', 'row_start': 2, 'max_rows': 1}, '')
            self.assertEqual(part['text'], '2026 Q3 | 125')
            coords = part['native_coordinates']; page = t.bundle['pages'][URL]
            self.assertEqual(page['content'][coords['native_start']:coords['native_end']], part['text'])
            self.assertEqual(page['raw_response_base64'], before['raw_response_base64'])
            self.assertEqual(page['retrieved_at_utc'], STAMP)
            self.assertEqual(page['sha256'], before['sha256'])
            self.assertTrue(t.bundle['page_history'][URL])
            self.assertTrue(part['evidence'])
            self.assertTrue(t.bundle['research_acquisition']['inspected_references'])
            self.assertTrue(t.bundle['research_acquisition']['pending_map_update'])
            self.assertIn('Revenue USD million', part['header_view']['text'])
            header = part['header_view']['native_coordinates']
            self.assertEqual(header['body_sha256'], part['native_coordinates']['body_sha256'])
            self.assertEqual(t.bundle['pages'][URL]['content'][header['native_start']:header['native_end']],
                             part['header_view']['text'])
            self.assertEqual(t.bundle['fetch_attempts'], [])
            wire.assert_not_called()

    def test_repeat_local_read_has_no_new_material_or_budget_on_resume(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, graph=True)
            args = {'url': URL, 'unit_id': 'table:1'}
            first = t.execute('intelligence_part', args, '')
            history = copy.deepcopy(t.bundle['page_history'])
            resumed = RetrievalTask(Path(root), t.bundle['request'])
            second = resumed.execute('intelligence_part', args, '')
            self.assertEqual(first['text'], second['text'])
            self.assertEqual(history, resumed.bundle['page_history'])
            self.assertEqual(resumed.bundle['research_acquisition']['events'][-1]['new_bodies'], [])
            self.assertEqual(resumed.bundle['fetch_attempts'], [])

    def test_navigation_reference_can_bind_an_observation_but_not_an_altered_number(self):
        from ForecastAgent.tests.test_research_simple_map import literal_proposal
        with tempfile.TemporaryDirectory() as root:
            t = task(root, graph=True)
            part = t.execute('intelligence_part', {'url': URL, 'unit_id': 'table:1', 'row_start': 2, 'max_rows': 1}, '')
            ref = next(r for r in part['evidence'] if part['text'] in r['text'])
            node = literal_proposal(t.bundle)['nodes'][0]
            node.update(claim=part['text'], evidence_ids=[ref['evidence_id']], event_stage='unknown',
                        event_time='', time_status='unknown')
            bound = state.bind_node(node, state.catalog(t.bundle))
            self.assertTrue(bound['literal_quote_verified'])
            self.assertFalse(bound['interpretation_verified'])
            node['claim'] = '2026 Q3 | 126'
            with self.assertRaisesRegex(ValueError, 'contiguous literal'):
                state.bind_node(node, state.catalog(t.bundle))

    def test_corrupted_original_or_truncated_wire_never_enters_navigation(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            t.bundle['pages'][URL]['sha256'] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'checksum'):
                t.execute('intelligence_outline', {'url': URL}, '')
            t.bundle['pages'][URL]['sha256'] = hashlib.sha256(RAW).hexdigest()
            t.bundle['pages'][URL]['raw_truncated'] = True
            with self.assertRaisesRegex(ValueError, 'incomplete raw'):
                t.execute('intelligence_part', {'url': URL, 'unit_id': 'table:1'}, '')

    def test_closed_tasks_allow_reading_but_do_not_mutate_material(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            t.bundle['result'] = {'status': 'complete'}
            before = copy.deepcopy(t.bundle['pages'])
            part = t.execute('intelligence_part', {'url': URL, 'unit_id': 'table:1'}, '')
            self.assertEqual(part['materialization_status'], 'read_only_closed_task')
            self.assertEqual(before, t.bundle['pages'])
            with self.assertRaisesRegex(ValueError, 'already finished'):
                t.execute('intelligence_fetch', FETCH, '')

    def test_invalid_arguments_cannot_spend_quota(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            for args in ({'url': URL, 'capture_id': 'x'}, {'url': URL, 'max_pages': 501}, {'url': URL, 'invented': True}):
                with self.assertRaises(ValueError):
                    t.execute('intelligence_outline', args, '')
            self.assertEqual(t.bundle['fetch_attempts'], [])

    def test_changed_implementation_identity_blocks_resume(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            identity = t.bundle['channel_tools']['identity']
            identity['code']['tools/original_navigation.py'] = 'different'; t.save()
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                RetrievalTask(Path(root), t.bundle['request'])


if __name__ == '__main__':
    unittest.main()
