"""Regression checks for lost context, partial visibility and resumed ledgers."""
from copy import deepcopy
import json
from tempfile import TemporaryDirectory
from unittest import TestCase

from ForecastAgent.runtime.context import collection_context, encode
from ForecastAgent.runtime.delivery import acknowledge, ensure_delivery_state
from ForecastAgent.runtime.collection_actions import duplicate_read
from ForecastAgent.runtime.collection_v2 import model_view
from ForecastAgent.tests.test_runtime_contracts import prepared, URL


def group(task, args, result, name='read_document', ident='read'):
    task.bundle['messages'] = [dict(role='system', content='Acquire information only'),
        dict(role='assistant', tool_calls=[{'id':ident, 'type':'function',
            'function':{'name':name, 'arguments':json.dumps(args)}}]),
        dict(role='tool', tool_call_id=ident, content=json.dumps(
            {'tool':name, 'ok':True, 'data':result}, ensure_ascii=False))]


def reply(messages):
    return json.loads(next(m['content'] for m in messages if m['role']=='tool'))['data']


class DeliveryTests(TestCase):
    def test_visible_spans_can_be_saved_without_model_rewriting(self):
        from ForecastAgent.runtime.collection_actions import pending_passages
        with TemporaryDirectory() as root:
            task = prepared(root)
            body = '| Date | Measurement |\n' + ''.join(f'| {i} | α={i} |\n' for i in range(500))
            task.bundle['pages'][URL]['content'] = body
            task.bundle['pages'][URL]['documents'] = []
            args = {'url':URL, 'max_chars':18000}
            group(task, args, task.execute('read_document', args, ''))
            view = collection_context(task)
            acknowledge(task, view)
            pending = pending_passages(task)
            self.assertGreater(len(pending), 0)
            result = task.execute('review_passages', {'items':[{'passage_id':p['passage_id'],
                'action':'keep', 'reason':'Retain the original measurement rows.', 'need_ids':['n']} for p in pending]}, '')
            self.assertTrue(all(row['ok'] for row in result['items']))
            self.assertEqual(''.join(e['text'] for e in task.bundle['excerpts']), reply(view)['content'])
            self.assertFalse(pending_passages(task))

    def test_restore_can_recover_handles_but_not_changed_source_versions(self):
        from ForecastAgent.runtime.delivery import recover_read_passages
        from ForecastAgent.runtime.collection_actions import pending_passages
        with TemporaryDirectory() as root:
            task = prepared(root)
            args = {'url':URL, 'max_chars':100}
            group(task, args, task.execute('read_document', args, ''))
            acknowledge(task, collection_context(task))
            task.bundle['passages'] = {}
            task.bundle['progress']['delivery_passages'] = {}
            recover_read_passages(task)
            self.assertTrue(pending_passages(task))
            task.bundle['passages'] = {}
            task.bundle['progress']['delivery_passages'] = {}
            task.bundle['pages'][URL]['content'] += ' Source changed.'
            recover_read_passages(task)
            self.assertFalse(pending_passages(task))

    def test_need_lifecycle_preserves_original_and_prevents_empty_scope(self):
        from ForecastAgent.runtime.needs import set_status, reconciliation
        from ForecastAgent.runtime.acquisition import checkpoint
        from ForecastAgent.runtime.contracts import ContractError
        with TemporaryDirectory() as root:
            task = prepared(root)
            original = deepcopy(task.bundle['plan'])
            obsolete = deepcopy(original[0])
            obsolete.update(id='old', condition='Weather forecast for remainder of August 2026')
            task.bundle['plan'].append(obsolete)
            task.bundle['control']['operating_clock_utc'] = '2026-10-01T00:00:00Z'
            plan = deepcopy(task.bundle['plan'])
            self.assertEqual(reconciliation(task)[0]['need_id'], 'old')
            set_status(task, {'need_id':'old', 'status':'not_applicable',
                'reason':'The event window has completed; realized records replace a forecast of its remainder.'})
            self.assertEqual(task.bundle['plan'], plan)
            self.assertNotIn('old', checkpoint(task)['needs_without_located_material'])
            self.assertFalse(reconciliation(task))
            with self.assertRaises(ContractError):
                set_status(task, {'need_id':'n', 'status':'not_applicable',
                    'reason':'This would incorrectly retire the remaining task objective.'})
            self.assertEqual(len(task.bundle['need_status_events']), 1)

    def test_newest_read_survives_large_state_with_exact_partial_coordinates(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            body = ''.join(f'Row {i:04d}: α data line.\n' for i in range(1500))
            task.bundle['pages'][URL]['content'] = body
            task.bundle['pages'][URL]['documents'] = []
            task.bundle['source_leads'].update({f'https://example.org/{i}':{'url':f'https://example.org/{i}'} for i in range(30)})
            task.bundle['plan'][0]['condition'] = 'Large plan description '*1000
            args = {'url':URL, 'max_chars':18000}
            result = task.execute('read_document', args, '')
            group(task, args, result)
            original = deepcopy(task.bundle['messages'])
            view = collection_context(task)
            data = reply(view)
            self.assertLessEqual(len(encode(view)), 28000)
            self.assertEqual(data['content'], body[:6000])
            self.assertEqual(data['end_char'], 6000)
            self.assertEqual(data['next_start'], 6000)
            self.assertEqual(data['delivery']['executed_end'], 18000)
            self.assertFalse(task.bundle['progress']['reads'])
            self.assertEqual(task.bundle['messages'], original)
            acknowledge(task, view)
            self.assertTrue(duplicate_read(task, {'url':URL, 'max_chars':6000}))
            self.assertFalse(duplicate_read(task, {'url':URL, 'start_char':6000, 'max_chars':6000}))
            self.assertEqual(list(task.bundle['progress']['reads'].values())[0]['end'], 6000)

    def test_execution_and_failed_request_do_not_confirm_delivery(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            args = {'url':URL, 'max_chars':100}
            group(task, args, task.execute('read_document', args, ''))
            view = collection_context(task)
            self.assertFalse(duplicate_read(task, args))
            # A provider exception never calls acknowledge.
            self.assertFalse(task.bundle['progress']['reads'])
            acknowledge(task, view)
            self.assertTrue(duplicate_read(task, args))
            self.assertFalse(next(iter(task.bundle['progress']['delivery_receipts'].values()))['comprehension_verified'])

    def test_legacy_read_claims_are_archived_without_resetting_provider_ledgers(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            legacy = {'old':{'scope':'unconfirmed', 'url':URL, 'start':0, 'end':500}}
            task.bundle['progress'] = {'reads':deepcopy(legacy)}
            task.bundle['searches'] = [{'status':'completed', 'query':'prior'}]
            baseline = deepcopy(task.bundle['searches'])
            ensure_delivery_state(task)
            self.assertEqual(task.bundle['progress']['legacy_unconfirmed_reads'], legacy)
            self.assertEqual(task.bundle['searches'], baseline)
            self.assertFalse(task.bundle['progress']['reads'])
            ensure_delivery_state(task)
            self.assertEqual(task.bundle['progress']['legacy_unconfirmed_reads'], legacy)

    def test_dataset_receipt_counts_only_complete_visible_rows(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            rows = [{'day':i, 'value':'x'*1000} for i in range(20)]
            task.bundle['pages'][URL]['rows'] = rows
            args = {'url':URL, 'offset':3, 'limit':17}
            result = task.execute('read_dataset_rows', args, '')
            group(task, args, result, 'read_dataset_rows')
            view = collection_context(task)
            data = reply(view)
            self.assertGreater(len(data['rows']), 0)
            self.assertLess(len(data['rows']), 17)
            self.assertEqual(data['rows'], rows[3:3+len(data['rows'])])
            acknowledge(task, view)
            interval = next(iter(task.bundle['progress']['reads'].values()))
            self.assertEqual(interval['end'], data['next_offset'])

    def test_bad_visible_coordinates_cannot_confirm_delivery(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            args = {'url':URL, 'max_chars':100}
            group(task, args, task.execute('read_document', args, ''))
            view = collection_context(task)
            tool = next(m for m in view if m['role']=='tool')
            value = json.loads(tool['content']); value['data']['content'] = 'fabricated'
            tool['content'] = json.dumps(value)
            with self.assertRaises(ValueError):
                acknowledge(task, view)
            self.assertFalse(task.bundle['progress']['reads'])

    def test_unbounded_navigation_keeps_text_but_still_removes_raw_and_quarantine(self):
        payload = {'url':URL, 'content':'A'*18000, 'raw_response_base64':'secret'}
        self.assertEqual(len(model_view(payload, text_limit=None)['content']), 18000)
        self.assertNotIn('raw_response_base64', model_view(payload, text_limit=None))
        self.assertNotIn('content', model_view(payload, {URL}, text_limit=None))
