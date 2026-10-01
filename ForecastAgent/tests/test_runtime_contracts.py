"""Failure/recovery scenarios for the independent acquisition runtime."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.runtime.contracts import validate, ContractError
from ForecastAgent.runtime.context import collection_context, MAX_CONTEXT_CHARS
from ForecastAgent.runtime import progress, session
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.skill_loader import freeze_skills, load_skill
from ForecastAgent.tests.test_collection import URL, page, call
from ForecastAgent.tests.test_collection_resume_repair import REQUEST, PLAN


LIVE = {**REQUEST, 'mode':'live', 'question':'Agency release before March', 'as_of_utc':None}
SEARCH = {'query':'Agency release', 'reason':'Find primary record', 'need_ids':['n'],
          'topic':'general', 'include_domains':[], 'include_domains_mode':'prefer',
          'exact_match':False, 'search_role':'primary'}


def prepared(directory, request=LIVE):
    task = RetrievalTask(Path(directory), request)
    task.bundle['plan'] = deepcopy(PLAN)
    task.bundle['pages'][URL] = page()
    freeze_skills(task.bundle)
    return task


class RuntimeContractsTests(TestCase):
    @patch('ForecastAgent.runtime.retrieval.search_batch')
    def test_wrong_need_and_unrelated_query_are_free_and_correctable(self, network):
        with TemporaryDirectory() as root:
            task = prepared(root)
            for args, code in (({**SEARCH, 'need_ids':['tavily_basic']}, 'unknown_need_id'),
                               ({**SEARCH, 'query':'dated_observations'}, 'ungrounded_query'),
                               ({**SEARCH, 'exact_match':'true'}, 'invalid_type')):
                with self.assertRaises(ContractError) as error:
                    validate(task, 'search_tavily', args, active_tools(task, COLLECTION_TOOLS))
                self.assertEqual(error.exception.details['code'], code if code != 'unknown_need_id' else 'invalid_choice')
            self.assertEqual(task.budget()['tavily_basic_remaining'], 3)
            network.assert_not_called()
            validate(task, 'search_tavily', SEARCH, active_tools(task, COLLECTION_TOOLS))

    def test_document_errors_include_valid_one_based_indices(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            with self.assertRaises(ContractError) as error:
                task.execute('read_document', {'url':URL, 'document_index':99}, '')
            self.assertEqual(error.exception.details['allowed_values'], [1, 2])
            self.assertIn('Second page', task.execute('read_document', {'url':URL, 'document_index':2}, '')['content'])
            with self.assertRaises(ContractError) as error:
                task.execute('read_document', {'url':URL, 'start_char':len(page()['content'])}, '')
            self.assertEqual(error.exception.details['code'], 'empty_read')

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page')
    def test_unknown_archive_replay_key_never_spends_http(self, fetch):
        with TemporaryDirectory() as root:
            task = prepared(root, REQUEST)
            with self.assertRaises(ContractError):
                task.execute('collect_archive', {'url':'https://web.archive.org/web/20260101/'+URL}, '')
            self.assertEqual(task.budget()['page_fetch_remaining'], 8)
            fetch.assert_not_called()

    def test_context_ceiling_preserves_protocol_and_loaded_frozen_skill(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            load_skill(task.bundle, 'economic-data')
            frozen = task.bundle['skill_bank']['economic-data']['content']
            task.bundle['request']['background'] = 'Large background '*10000
            task.bundle['source_leads'].update({f'https://example.org/{i}?q='+('x'*2000):{'url':f'https://example.org/{i}?q='+('x'*2000)} for i in range(80)})
            task.bundle['messages'] = [{'role':'system', 'content':'Acquisition only'}]
            for i in range(4):
                task.bundle['messages'] += [dict(role='assistant', tool_calls=[{'id':str(i), 'type':'function', 'function':{'name':'list_sources', 'arguments':'{}'}}]),
                    dict(role='tool', tool_call_id=str(i), content=json.dumps({'urls':['z'*5000]*100}))]
            original = deepcopy(task.bundle['messages'])
            projected = collection_context(task)
            self.assertLessEqual(len(json.dumps(projected, ensure_ascii=False, separators=(',', ':'))), MAX_CONTEXT_CHARS)
            self.assertIn(frozen, projected[0]['content'])
            self.assertNotIn(task.bundle['skill_bank']['ai-releases']['content'], projected[0]['content'])
            ids = {c['id'] for m in projected for c in m.get('tool_calls', [])}
            self.assertTrue(all(m['tool_call_id'] in ids for m in projected if m['role']=='tool'))
            self.assertEqual(task.bundle['messages'], original)

    def test_context_does_not_expose_audit_only_text_or_raw_payload(self):
        with TemporaryDirectory() as root:
            task = prepared(root, REQUEST)
            task.bundle['pages'][URL]['temporal_status'] = 'current_capture_possible_later_edits'
            task.bundle['messages'] = [dict(role='system', content='Collection only'),
                dict(role='assistant', tool_calls=[{'id':'r', 'function':{'name':'read_document', 'arguments':'{}'}}]),
                dict(role='tool', tool_call_id='r', content=json.dumps({'url':URL,'content':'LATER OUTCOME SECRET', 'raw_response':{'private':'RAW SECRET'}}))]
            projected = json.dumps(collection_context(task))
            self.assertNotIn('LATER OUTCOME SECRET', projected)
            self.assertNotIn('RAW SECRET', projected)

    def test_overlapping_reads_and_catalogs_are_not_new_material(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            before = progress.snapshot(task)
            args = {'url':URL, 'start_char':0, 'max_chars':100}
            result = task.execute('read_document', args, '')
            progress.delivered(task, 'read_document', args, result)
            self.assertTrue(progress.delta(before, progress.snapshot(task))['material_progress'])
            before = progress.snapshot(task)
            args.update(start_char=10, max_chars=50)
            progress.delivered(task, 'read_document', args, task.execute('read_document', args, ''))
            task.execute('list_documents', {}, '')
            self.assertFalse(progress.delta(before, progress.snapshot(task))['advanced'])
            args.update(start_char=100)
            progress.delivered(task, 'read_document', args, task.execute('read_document', args, ''))
            self.assertTrue(progress.delta(before, progress.snapshot(task))['advanced'])

    def test_blocked_body_read_and_duplicate_excerpt_cannot_inflate_progress(self):
        with TemporaryDirectory() as root:
            task = prepared(root, REQUEST)
            task.bundle['pages'][URL]['temporal_status'] = 'current_capture_possible_later_edits'
            before = progress.snapshot(task)
            response = task.execute('read_document', {'url':URL}, '')
            progress.delivered(task, 'read_document', {'url':URL}, response)
            self.assertFalse(progress.delta(before, progress.snapshot(task))['advanced'])
            task.bundle['pages'][URL]['temporal_status'] = 'local_pre_cutoff_capture'
            task.bundle['pages'][URL]['retrieved_at_utc'] = '2026-01-01T00:00:00Z'
            args = {'url':URL, 'quote':'Second page publication.', 'document_index':2, 'need_ids':['n']}
            task.execute('record_quote', args, '')
            before = progress.snapshot(task)
            task.execute('record_quote', args, '')
            self.assertFalse(progress.delta(before, progress.snapshot(task))['advanced'])

    def test_local_replay_is_version_bound_and_survives_restore(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            args = {'url':URL}
            key = session.cache_key(task, 'read_document', args)
            session.save_local(task, key, task.execute('read_document', args, ''))
            task.save()
            restored = RetrievalTask(Path(root), LIVE)
            self.assertTrue(session.replay_local(restored, session.cache_key(restored, 'read_document', args))['cached_local_reply'])
            restored.bundle['pages'][URL]['content'] += 'New version'
            self.assertIsNone(session.replay_local(restored, session.cache_key(restored, 'read_document', args)))

    def test_modified_local_reply_is_refused_instead_of_replayed(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            key = session.cache_key(task, 'read_document', {'url':URL})
            session.save_local(task, key, task.execute('read_document', {'url':URL}, ''))
            path = Path(root)/'tool_outputs'/f'{key}.json'
            saved = json.loads(path.read_text(encoding='utf-8'))
            saved['result']['content'] = 'Modified text'
            path.write_text(json.dumps(saved), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                session.replay_local(task, key)

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_dispatch_budget_stops_and_remains_resumable_without_resetting_attempts(self, model):
        def reserve_dispatch(messages, key, **kwargs):
            observer = kwargs['observer']
            from ForecastAgent.runtime.limits import MODEL_HTTP_PER_DISPATCH
            for i in range(MODEL_HTTP_PER_DISPATCH):
                record = {'started_at_utc':'2026-10-01T00:00:00Z', 'retry_index':i, 'status':'reserved'}
                token = observer('reserve', record)
                observer('complete', {**record, 'status':'received'}, token)
            return call('list_sources', {}, 'list')
        model.side_effect = reserve_dispatch
        with TemporaryDirectory() as root:
            task = prepared(root); task.bundle['model_attempts'] = [{'id':1,'status':'received'}]; task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result['session_state'], 'budget_exhausted')
            self.assertTrue(result['result']['resumable'])
            self.assertTrue(result['result']['incomplete'])
            self.assertEqual(len(result['model_attempts']), 17)
            model.side_effect = [call('finish_collection', {'gaps':['Insufficient sources']}, 'end')]
            restored = run_retrieval(LIVE, root, '', '')
            self.assertEqual(len(restored['model_attempts']), 17)
            self.assertEqual(restored['session_state'], 'completed_with_gaps')

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_mixed_bad_call_and_catalog_success_does_not_hide_stall(self, model):
        invalid = call('read_document', {'url':URL, 'document_index':99}, 'invalid')
        listing = call('list_sources', {}, 'list')
        batch = {'tool_calls':invalid['tool_calls']+listing['tool_calls']}
        model.side_effect = [batch, batch, batch, call('finish_collection', {'gaps':['No progress']}, 'end')]
        with TemporaryDirectory() as root:
            task = prepared(root); task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(model.call_count, 4)
            self.assertTrue(all(t['failed'] and not t['advanced'] for t in result['sessions'][-1]['turns'][:3]))
            self.assertEqual(result['result']['session_state'], 'completed_with_gaps')
            self.assertEqual(result['result']['termination_reason'], 'stalled')

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_duplicate_successful_read_is_blocked_without_claiming_progress(self, model):
        model.side_effect = [call('read_document', {'url':URL, 'max_chars':100}, 'one'),
            call('read_document', {'url':URL, 'max_chars':100}, 'two'),
            call('finish_collection', {'gaps':['Missing further source']}, 'end')]
        with TemporaryDirectory() as root:
            task = prepared(root); task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(result['transcript'][1]['result']['data']['contract_error']['code'],'already_delivered_range')
            self.assertFalse(result['sessions'][-1]['turns'][1]['advanced'])

    @patch('ForecastAgent.runtime.retrieval.search_batch')
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_interrupted_paid_reservation_is_not_reissued_after_restore(self, model, network):
        with TemporaryDirectory() as root:
            task = prepared(root)
            task.bundle['searches'] = [{'status':'reserved','results':[], 'query':SEARCH['query']}]
            signature = ['search_tavily', json.dumps(SEARCH, sort_keys=True)]
            task.bundle['control']['seen_calls'] = [signature]
            task.bundle['result'] = {'incomplete':True}
            task.bundle['messages'] = [dict(role='system',content='Collection'),
                dict(role='assistant',tool_calls=call('search_tavily',SEARCH,'interrupted')['tool_calls'])]
            task.bundle['sessions'] = [{'id':1,'state':'running'}]
            task.save()
            model.side_effect = [call('search_tavily', SEARCH, 'again'), call('finish_collection', {'gaps':['Unknown interrupted response']}, 'end')]
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(len(result['searches']), 1)
            self.assertEqual(result['transcript'][0]['result']['data']['contract_error']['code'], 'duplicate_attempt')
            self.assertEqual(result['sessions'][0]['state'], 'interrupted')
            self.assertEqual(result['sessions'][-1]['budgets_before']['tavily_basic_remaining'], 2)
            network.assert_not_called()

    @patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=RuntimeError('temporary transport outage'))
    def test_transport_failure_preserves_state_and_has_retryable_status(self, model):
        with TemporaryDirectory() as root:
            task = prepared(root); task.bundle['searches'] = [{'status':'failed','results':[]}]; task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(result['session_state'], 'retryable_failure')
            self.assertTrue(result['result']['resumable'])
            self.assertEqual(len(result['searches']), 1)
            model.side_effect = [call('finish_collection', {'gaps':['No extra search needed']}, 'end')]
            restored = run_retrieval(LIVE, root, '', '')
            self.assertEqual(len(restored['sessions']), 2)
            self.assertEqual(len(restored['searches']), 1)

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_lifetime_exhaustion_exports_without_another_model_request(self, model):
        with TemporaryDirectory() as root:
            task = prepared(root); task.bundle['model_attempts'] = [{'id':i,'status':'reserved'} for i in range(72)]; task.save()
            result = run_retrieval(LIVE, root, '', '')
            self.assertEqual(result['session_state'], 'budget_exhausted')
            self.assertFalse(result['result']['resumable'])
            self.assertEqual(len(result['model_attempts']), 72)
            self.assertTrue((Path(root)/'intelligence.json').exists())
            run_retrieval(LIVE, root, '', '')
            model.assert_not_called()

    def test_channel_and_skill_enums_do_not_contaminate_unrelated_strings(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            tools = active_tools(task, COLLECTION_TOOLS)
            quote = next(t['function']['parameters']['properties']['quote'] for t in tools if t['function']['name']=='record_quote')
            self.assertNotIn('enum', quote)
            validate(task, 'record_quote', {'url':URL, 'quote':'Second page publication.', 'document_index':2, 'need_ids':['n']}, tools)
