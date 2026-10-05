"""Check real saved stall sequences and the boundaries of delivery draining."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition.frontier_compare import inputs, seeded, run_arm, request_for
from ForecastAgent.runtime.context import collection_context
from ForecastAgent.runtime.delivery import acknowledge
from ForecastAgent.runtime.delivery_control import pending_reads, POLICY_FIELD
from ForecastAgent.runtime.progress import snapshot
from ForecastAgent.tests.test_delivery import group
from ForecastAgent.tests.test_runtime_contracts import prepared, URL

FIXTURE = Path(__file__).parents[1]/'fixtures/delivery_stall_saved_calls.json'


class DeliveryDrainTests(TestCase):
    def read_task(self, root):
        task = prepared(root)
        task.bundle['request'][POLICY_FIELD] = True
        args = {'url': URL, 'start_char': 0, 'max_chars': 100}
        group(task, args, task.execute('read_document', args, ''))
        return task, args

    def test_new_saved_read_is_pending_without_granting_progress_or_receipts(self):
        with TemporaryDirectory() as root:
            task, _ = self.read_task(root)
            before = copy.deepcopy(task.bundle)
            self.assertEqual(len(pending_reads(task)), 1)
            self.assertEqual(task.bundle, before)
            self.assertEqual(pending_reads(task)[0]['undelivered_ranges'], [(0, 100)])

    def test_delivered_or_overlapping_reads_are_neutral_even_with_new_call_ids(self):
        with TemporaryDirectory() as root:
            task, _ = self.read_task(root)
            acknowledge(task, collection_context(task))
            self.assertFalse(pending_reads(task))
            args = {'url': URL, 'start_char': 10, 'max_chars': 80}
            group(task, args, task.execute('read_document', args, ''), ident='overlap')
            before = snapshot(task)
            self.assertFalse(pending_reads(task))
            self.assertEqual(snapshot(task), before)

    def test_partial_delivery_only_reports_uncovered_tail(self):
        with TemporaryDirectory() as root:
            task, _ = self.read_task(root)
            acknowledge(task, collection_context(task))
            args = {'url': URL, 'start_char': 50, 'max_chars': 100}
            group(task, args, task.execute('read_document', args, ''), ident='tail')
            self.assertEqual(pending_reads(task)[0]['undelivered_ranges'], [(100, 150)])

    def test_failure_fabrication_changed_version_and_incomplete_batch_never_drain(self):
        with TemporaryDirectory() as root:
            task, _ = self.read_task(root)
            original = copy.deepcopy(task.bundle)
            for change in ('failure', 'text', 'raw_hash', 'incomplete'):
                task.bundle = copy.deepcopy(original)
                value = json.loads(task.bundle['messages'][-1]['content'])
                if change == 'failure': value['ok'] = False
                if change == 'text': value['data']['content'] = 'fabricated'
                if change == 'raw_hash': task.bundle['pages'][URL]['sha256'] = 'changed-version'
                if change == 'incomplete': task.bundle['messages'][1]['tool_calls'].append({'id':'missing','function':{'name':'list_documents','arguments':'{}'}})
                task.bundle['messages'][-1]['content'] = json.dumps(value)
                self.assertFalse(pending_reads(task), change)

    def test_old_unseen_group_and_legacy_claims_cannot_keep_catalog_loops_alive(self):
        with TemporaryDirectory() as root:
            task, _ = self.read_task(root)
            task.bundle.setdefault('progress', {})['reads'] = {'legacy':{'scope':'unknown', 'start':0, 'end':100}}
            self.assertTrue(pending_reads(task))
            task.bundle['messages'].append({'role':'assistant','tool_calls':[]})
            self.assertFalse(pending_reads(task))

    def test_control_can_be_audited_without_enabling_the_exception(self):
        with TemporaryDirectory() as root:
            task, _ = self.read_task(root)
            task.bundle['request'].pop(POLICY_FIELD)
            before = copy.deepcopy(task.bundle)
            self.assertFalse(pending_reads(task))
            self.assertTrue(pending_reads(task, inspect_only=True))
            self.assertEqual(task.bundle, before)

    def test_pair_changes_only_opt_in_control_and_refuses_prior_task_migration(self):
        pool, _ = inputs()
        with TemporaryDirectory() as root:
            a = seeded(pool['cases'][0], 'baseline', Path(root)/'a', repair_delivery=True)
            b = seeded(pool['cases'][0], 'candidate', Path(root)/'b', repair_delivery=True)
            ar, br = copy.deepcopy(a.bundle['request']), copy.deepcopy(b.bundle['request'])
            self.assertTrue(br.pop(POLICY_FIELD))
            self.assertEqual(ar, br)
            self.assertEqual(a.bundle['pages'], b.bundle['pages'])
            self.assertEqual(a.budget(), b.budget())
            with self.assertRaisesRegex(ValueError, 'different input'):
                seeded(pool['cases'][0], 'candidate', Path(root)/'b', repair_v3=True)
            with self.assertRaisesRegex(ValueError, 'one separately frozen'):
                request_for(pool['cases'][0], 'candidate', repair_v3=True, repair_delivery=True)

    def replay(self, qid, enabled, root, *, transport_failure=False, turns=None):
        pool, _ = inputs()
        case = next(c for c in pool['cases'] if c['id'] == qid)
        saved = next(c for c in json.loads(FIXTURE.read_text(encoding='utf-8'))['cases'] if c['question_id'] == qid)['calls']
        seen = []
        def respond(messages, *args, **kwargs):
            index = len(seen)
            seen.append(kwargs.get('forced_tool'))
            if index < len(saved):
                return copy.deepcopy(saved[index])
            if transport_failure:
                raise RuntimeError('Provider temporarily unavailable')
            focus = next((json.loads(m['content']) for m in messages if m.get('role') == 'user'
                and json.loads(m['content']).get('kind') == 'pending_material_review'), None)
            if focus:
                self.assertEqual(kwargs['forced_tool'], 'review_passages')
                needs = [n['id'] for n in load_bundle()['plan']]
                tool, args = 'review_passages', {'items':[{'passage_id':p['passage_id'], 'action':'keep',
                    'need_ids':needs, 'reason':'Preserve the exact saved span for later analysis; no event verdict.'} for p in focus['passages']]}
            else:
                tool, args = 'finish_collection', {'gaps':['Full rule coverage and independent material adequacy remain unverified.']}
            return {'tool_calls':[{'id':'drain-'+str(index), 'type':'function',
                'function':{'name':tool, 'arguments':json.dumps(args)}}]}
        def load_bundle():
            return json.loads((root/'bundle.json').read_text(encoding='utf-8'))
        with patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=respond), \
             patch('ForecastAgent.providers.http.download') as network, \
             patch('ForecastAgent.runtime.retrieval.COLLECTION_MAX_TURNS', turns or 12):
            report = run_arm(case, 'candidate' if enabled else 'baseline', root, 'mock-key',
                model='nvidia/nemotron-3-super-120b-a12b:free', http_cap=11, repair_delivery=True)
        network.assert_not_called()
        return report, load_bundle(), len(seen)

    def test_real_hack_and_gas_sequences_deliver_then_review_without_reset(self):
        for qid in ('43501', '44801'):
            with self.subTest(qid=qid), TemporaryDirectory() as root:
                old, ob, oc = self.replay(qid, False, Path(root)/'old')
                new, nb, nc = self.replay(qid, True, Path(root)/'new')
                self.assertEqual(old['termination_reason'], 'stalled')
                self.assertEqual(len(nb['control']['delivery_drain_events']), 1)
                event = nb['control']['delivery_drain_events'][0]
                self.assertFalse(event['progress_credit_granted'])
                self.assertFalse(event['budget_reset'])
                self.assertGreater(new['banked_chars'], old['banked_chars'])
                self.assertFalse(pending_reads(type('Task', (), {'bundle':nb, 'verified_only':False})()))
                receipts = nb['progress']['delivery_receipts'].values()
                self.assertTrue(any(r.get('tool_call_id') == event['pending_reads'][0]['tool_call_id'] for r in receipts))
                self.assertEqual(ob['pages'], nb['pages'])
                self.assertEqual(new['issues'], [])
                self.assertEqual(nc, oc+2)

    def test_hard_decision_stop_and_transport_failure_remain_binding(self):
        with TemporaryDirectory() as root:
            report, bundle, calls = self.replay('43501', True, Path(root)/'hard', turns=6)
            self.assertEqual(calls, 4)
            self.assertFalse(bundle['control'].get('delivery_drain_events'))
        with TemporaryDirectory() as root:
            report, bundle, calls = self.replay('43501', True, Path(root)/'failed', transport_failure=True)
            self.assertEqual(calls, 6)
            self.assertEqual(report['termination_reason'], 'model_transport_failure')
            self.assertEqual(len(bundle['control']['delivery_drain_events']), 1)
            self.assertTrue(pending_reads(type('Task', (), {'bundle':bundle, 'verified_only':False})()))
            self.assertFalse(bundle['progress'].get('reads'))
