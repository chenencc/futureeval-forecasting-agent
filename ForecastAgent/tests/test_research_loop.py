"""Offline safety, incremental state, equivalent raw coverage and durable scoring."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import save, load, digest
from ForecastAgent.analysis.distributions import validate_cdf
from ForecastAgent.research_loop import POLICY
from ForecastAgent.research_loop import state, runtime, decision
from ForecastAgent.research_loop.__main__ import replay
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.progress import snapshot
from ForecastAgent.tests.test_competition_mercury import bundle as fixture, response


def source_bundle(kind='binary'):
    b = fixture(kind)
    b['request'].update(research_state_policy=POLICY, pipeline='collection', mode='live')
    b['pipeline'] = 'collection'
    body = ('Official company report for the target quarter. Revenue was 24 billion dollars. '
            'Management guidance is conditional on deliveries and product demand.\n' * 14)
    b['pages']['https://example.org/report']['content'] = body
    from ForecastAgent.readers.quality import body_diagnostics
    b['pages']['https://example.org/report']['body_diagnostics'] = body_diagnostics(body)
    b['searches'] = [{'status': 'received'}]
    b['exa_searches'] = [{'status': 'received'}]
    b['fetch_attempts'] = [{'status': 'received'}]
    return b


def proposal(b, revision=0, correction=False):
    material = state.catalog(b)
    handle = next(iter(material['spans']))
    return {'expected_revision': revision, 'revision_kind': 'interpretation_correction' if correction else 'material_update',
        'material_sha256': material['material_sha256'], 'nodes': [
            {'id': 'observed_report', 'kind': 'observation', 'claim': 'The saved report states quarterly revenue.',
             'evidence_ids': [handle], 'event_time': '', 'time_status': 'unknown', 'gap_reason': 'none'},
            {'id': 'demand', 'kind': 'driver', 'claim': 'Future demand could change the target outcome.',
             'evidence_ids': [handle], 'event_time': '', 'time_status': 'hypothesized', 'gap_reason': 'ambiguous'},
            {'id': 'unpublished', 'kind': 'unknown', 'claim': 'The final target release may still be unpublished.',
             'evidence_ids': [], 'event_time': '', 'time_status': 'unknown', 'gap_reason': 'not_yet_published'}],
        'relations': [{'from_id': 'demand', 'to_id': 'unpublished', 'kind': 'causal',
                       'rationale': 'A proposed driver, not an established causal relationship.'}],
        'supporting_path': 'Guidance and the target period could be consistent with the event.',
        'alternative_path': 'Different demand or a changed scope could produce another outcome.',
        'material_requests': [{'target': 'Exact target-period release', 'reason': 'Retain the actual metric and units.',
            'node_ids': ['unpublished'], 'role': 'primary', 'suggested_tool': 'read_sources'}],
        'retired_node_ids': [], 'revision_reason': 'Organize the newly captured report.'}


class ResearchStateTests(unittest.TestCase):
    def test_exact_bindings_no_raw_or_search_mutation_and_idempotency(self):
        b = source_bundle(); raw = copy.deepcopy({k: b[k] for k in ('pages', 'searches', 'exa_searches', 'fetch_attempts')})
        p = proposal(b); first = state.update(b, p); again = state.update(b, p)
        self.assertEqual(first['revision'], 1); self.assertTrue(again['cached'])
        self.assertEqual(raw, {k: b[k] for k in raw})
        bound = b['research_loop']['current']['nodes'][0]['bindings'][0]
        body = b['pages'][bound['url']]['content']
        self.assertEqual(body[bound['start']:bound['end']], bound['text'])
        self.assertFalse(bound['meaning_verified']); self.assertFalse(b['research_loop']['probability_head'])
        self.assertEqual(state.audit(b)['status'], 'bound_unverified')

    def test_unknown_reference_and_missing_alternative_are_atomic(self):
        for mutate in (lambda p: p['nodes'][0].update(evidence_ids=['Rmissing']),
                       lambda p: p.update(alternative_path=''),
                       lambda p: p['nodes'][0].update(evidence_ids=[]),
                       lambda p: p.update(probability=.8),
                       lambda p: p['relations'][0].update(to_id='missing')):
            with self.subTest(mutation=str(mutate)):
                b = source_bundle(); state.initialize(b); before = copy.deepcopy(b)
                p = proposal(b); mutate(p)
                with self.assertRaises(ValueError): state.update(b, p)
                self.assertEqual(before, b)

    def test_stale_material_and_tampered_body_rejected(self):
        b = source_bundle(); p = proposal(b)
        b['pages']['https://example.org/report']['content'] += '\nA new revision.'
        with self.assertRaisesRegex(ValueError, 'Material changed'): state.update(b, p)
        b['pages']['https://example.org/report']['content_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'checksum'): state.catalog(b)

    def test_empty_shell_and_search_results_cannot_support_observations(self):
        b = source_bundle()
        b['pages'] = {'https://example.org/blocked': {'content': 'Access denied'},
                      'https://example.org/empty': {'content': ''}}
        self.assertFalse(state.catalog(b)['spans'])
        self.assertEqual(len(state.catalog(b)['excluded_sources']), 2)
        p = proposal(source_bundle()); p['material_sha256'] = state.catalog(b)['material_sha256']
        with self.assertRaises(ValueError): state.update(b, p)

    def test_future_missing_evidence_remains_unknown(self):
        b = source_bundle(); state.update(b, proposal(b))
        node = b['research_loop']['current']['nodes'][2]
        self.assertEqual(node['kind'], 'unknown'); self.assertEqual(node['gap_reason'], 'not_yet_published')
        self.assertFalse(node['bindings']); self.assertNotIn('probability', node)
        p = proposal(b, 1, True); p['nodes'][2]['time_status'] = 'source_stated'
        with self.assertRaises(ValueError): state.update(b, p)

    def test_source_stated_time_must_be_literal_and_is_not_truth_certified(self):
        b = source_bundle(); p = proposal(b)
        p['nodes'][0].update(event_time='2027-10-31', time_status='source_stated')
        with self.assertRaisesRegex(ValueError, 'literal'): state.update(b, p)
        p['nodes'][0]['event_time'] = 'target quarter'
        state.update(b, p)
        self.assertFalse(b['research_loop']['current']['nodes'][0]['interpretation_verified'])

    def test_explicit_correction_retirement_and_lifetime_cap(self):
        b = source_bundle(); state.update(b, proposal(b))
        p = proposal(b, 1)
        with self.assertRaisesRegex(ValueError, 'No new material'): state.update(b, p)
        p['revision_kind'] = 'interpretation_correction'; p['nodes'].pop(1)
        p['relations'] = []; p['retired_node_ids'] = ['demand']
        state.update(b, p)
        self.assertEqual(b['research_loop']['events'][0]['state']['nodes'][1]['id'], 'demand')
        p['expected_revision'] = 2; p['retired_node_ids'] = []; p['alternative_path'] = 'A revised alternative remains possible.'
        state.update(b, p)
        p['expected_revision'] = 3; p['alternative_path'] = 'Another correction.'
        with self.assertRaisesRegex(ValueError, 'cap exhausted'): state.update(b, p)
        self.assertEqual(state.audit(b)['remaining_updates'], 0)

    def test_removed_nodes_and_unchanged_corrections_rejected(self):
        b = source_bundle(); state.update(b, proposal(b))
        p = proposal(b, 1, True)
        with self.assertRaisesRegex(ValueError, 'Unchanged'): state.update(b, p)
        p['nodes'].pop(1); p['relations'] = []
        with self.assertRaisesRegex(ValueError, 'explicitly retired'): state.update(b, p)

    def test_new_source_is_partial_but_changed_binding_invalidates_map(self):
        b = source_bundle(); state.update(b, proposal(b))
        b['pages']['https://example.net/alternative'] = {'content': 'A contrary observation in another original report. ' * 20}
        report = state.audit(b)
        self.assertTrue(report['usable']); self.assertEqual(report['status'], 'partial_new_material')
        b['pages']['https://example.org/report']['content'] += ' Correction.'
        self.assertFalse(state.audit(b)['usable'])

    def test_journal_rules_and_state_tampering_detected(self):
        for target in ('event', 'current', 'rules'):
            b = source_bundle(); state.update(b, proposal(b))
            if target == 'event': b['research_loop']['events'][0]['at_utc'] = 'changed'
            elif target == 'current': b['research_loop']['current'] = {}
            else: b['request']['resolution_criteria'] += ' changed'
            with self.assertRaises(ValueError): state.audit(b)


class ResearchRuntimeTests(unittest.TestCase):
    def task(self, root, enabled=True):
        request = source_bundle()['request']
        if not enabled: request.pop('research_state_policy')
        task = RetrievalTask(Path(root), request)
        task.bundle['pages'] = source_bundle()['pages']; task.save()
        return task

    def test_opt_in_tool_execution_persistence_and_no_acquisition_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(tmp); before = snapshot(task)
            inspected = task.execute('inspect_research_state', {'limit': 2}, 'unused')
            self.assertIn('text', inspected['evidence'][0])
            result = task.execute('update_research_state', proposal(task.bundle), 'unused')
            self.assertTrue(result['no_progress']); self.assertEqual(before, snapshot(task))
            restored = RetrievalTask(Path(tmp), task.bundle['request'])
            self.assertEqual(restored.bundle['research_loop']['revision'], 1)
            self.assertEqual(restored.bundle['searches'], task.bundle['searches'])

    def test_disabled_and_forced_closure_do_not_require_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(tmp, False)
            self.assertEqual(runtime.configure(task, []), [])
            self.assertNotIn('research_loop', task.bundle)
            with self.assertRaises(ValueError): task.execute('inspect_research_state', {}, '')
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(tmp); tools = runtime.configure(task, [])
            task.bundle['control']['forced_close'] = True
            self.assertFalse(runtime.filter_tools(task, tools))

    def test_bounded_context_preserves_rules_and_map(self):
        from ForecastAgent.runtime.context import collection_context
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(tmp); state.update(task.bundle, proposal(task.bundle))
            task.bundle['messages'] = [{'role': 'system', 'content': runtime.guide(task, 'Collect original evidence.')}]
            messages = collection_context(task)
            self.assertIn('research_map', messages[1]['content'])
            self.assertIn('INITIAL release', messages[1]['content'])

    def test_real_collection_loop_exposes_tools_and_resumes_terminal_without_calls(self):
        from ForecastAgent.runtime.retrieval import run_retrieval, utc_now
        from ForecastAgent.tests.test_collection import PLAN, call
        from ForecastAgent.providers.model import SUPER_MODEL
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'EXA_API_KEY': '', 'FORECAST_MODEL': SUPER_MODEL}):
            task = self.task(tmp)
            task.execute('plan_evidence', PLAN, '')
            task.save(); p = proposal(task.bundle); turns = []
            def ask(messages, key, tools, forced_tool, observer, deadline):
                number = len(turns)
                name, args = [('inspect_research_state', {'limit': 2}),
                              ('update_research_state', p), ('finish_collection', {'gaps': ['No independent source']})][number]
                self.assertIn(name, [t['function']['name'] for t in tools])
                self.assertIn('fallible', messages[0]['content'])
                record = {'request': {'model': SUPER_MODEL}, 'status': 'reserved',
                          'started_at_utc': utc_now(), 'retry_index': 0}
                token = observer('reserve', record)
                record.update(status='received', response={'usage': {'total_tokens': 123}})
                observer('complete', record, token); turns.append(name)
                return call(name, args, str(number))
            with patch('ForecastAgent.runtime.retrieval.ask_ultra', ask), patch('urllib.request.urlopen', side_effect=AssertionError('No network')):
                output = run_retrieval(task.bundle['request'], Path(tmp), '', '')
                restored = run_retrieval(task.bundle['request'], Path(tmp), '', '')
            self.assertEqual(turns, ['inspect_research_state', 'update_research_state', 'finish_collection'])
            self.assertEqual(output['research_loop']['revision'], 1)
            self.assertEqual(len(output['model_attempts']), 3)
            self.assertFalse(output['searches']); self.assertFalse(output['exa_searches'])
            self.assertEqual(restored['research_loop'], output['research_loop'])


class ResearchDecisionTests(unittest.TestCase):
    def test_paired_coverage_no_probability_head_and_byte_bounds(self):
        b = source_bundle(); state.update(b, proposal(b)); packed = decision.prepare(b)
        for key in packed['baseline']: self.assertEqual(packed['baseline'][key], packed['enriched'][key])
        self.assertTrue(packed['audit']['equal_original_coverage'])
        self.assertLessEqual(packed['audit']['enriched_bytes'], decision.BYTE_CAP)
        self.assertFalse(b['research_loop']['probability_head'])

    def test_unbuilt_or_stale_map_has_direct_evidence_fallback(self):
        b = source_bundle()
        self.assertEqual(decision.prepare(b)['enriched']['research_map']['status'], 'unbuilt')
        state.update(b, proposal(b)); b['pages']['https://example.org/report']['content'] += ' Revised.'
        self.assertEqual(decision.prepare(b)['enriched']['research_map']['status'], 'invalid_bindings')

    def test_observations_outside_visible_coverage_omitted(self):
        b = source_bundle(); state.update(b, proposal(b))
        base = decision.prepare(b)['baseline']; base['evidence'] = []
        notes, _ = decision.scoring_map(b, base)
        self.assertEqual(notes['status'], 'map_omitted_size_or_coverage_limit')
        self.assertNotIn('nodes', notes)

    def test_all_types_cached_scoring_format_and_clip(self):
        for kind in ('binary', 'multiple_choice', 'numeric', 'date', 'discrete'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                b = source_bundle(kind); state.update(b, proposal(b)); attempts = []
                def decide(state_input, registry, key, observer):
                    record = {'request': {'state': state_input}, 'status': 'reserved'}
                    token = observer('reserve', record); attempts.append(1)
                    answer = response(registry); record.update(status='received', response=answer)
                    observer('complete', record, token); return answer
                with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch('ForecastAgent.providers.decisions.decide', decide):
                    result = decision.run(b, tmp, execute=True)
                    again = decision.run(b, tmp, execute=True)
                self.assertEqual(result, again); self.assertEqual(attempts, [1]); self.assertFalse(result['submitted'])
                candidate = result['payload']
                if kind == 'binary': self.assertEqual(candidate['probability_yes'], .98)
                elif kind == 'multiple_choice': self.assertAlmostEqual(sum(candidate['probability_yes_per_category'].values()), 1)
                else: validate_cdf(candidate['continuous_cdf'], b['request'], clipped=True)

    def test_dry_run_and_outcome_labels_never_call_provider(self):
        with tempfile.TemporaryDirectory() as tmp, patch('ForecastAgent.providers.decisions.decide') as provider:
            self.assertEqual(decision.run(source_bundle(), tmp)['status'], 'prepared')
            b = source_bundle(); b['request']['resolution'] = 'yes'
            with self.assertRaises(ValueError): decision.prepare(b)
            provider.assert_not_called()

    def test_exhausted_attempt_no_restart_and_changed_identity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = source_bundle()
            def fail(state_input, registry, key, observer):
                observer('reserve', {'status': 'reserved'})
                raise RuntimeError('Simulated interruption')
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch('ForecastAgent.providers.decisions.decide', fail):
                with self.assertRaises(RuntimeError): decision.run(b, tmp, execute=True)
                with self.assertRaisesRegex(RuntimeError, 'cap exhausted'): decision.run(b, tmp, execute=True)
            b['pages']['https://example.org/report']['content'] += ' changed'
            with self.assertRaisesRegex(ValueError, 'Frozen'): decision.run(b, tmp)

    def test_offline_child_replay_preserves_parent_and_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); b = source_bundle(); b['request'].pop('research_state_policy')
            parent = root/'parent.json'; save(parent, b); initial = parent.read_bytes()
            proposed = root/'proposal.json'; save(proposed, proposal(b))
            replay(parent, proposed, root/'child')
            original_child = (root/'child/research-package.json').read_bytes()
            replay(parent, proposed, root/'child')
            self.assertEqual((root/'child/research-package.json').read_bytes(), original_child)
            self.assertEqual(parent.read_bytes(), initial)
            child = load(root/'child/research-package.json')
            self.assertEqual(child['pages'], b['pages']); self.assertEqual(child['searches'], b['searches'])
            self.assertEqual(child['research_loop']['revision'], 1)


if __name__ == '__main__':
    unittest.main()
