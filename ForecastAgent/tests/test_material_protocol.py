"""Offline V3 controls exercised on preserved V2 arguments and source bytes."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition.frontier_compare import inputs, request_for
from ForecastAgent.acquisition.pipeline import prepare, verify_baseline
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.intelligent_acquisition import assess, configure_tools, terminal_report
from ForecastAgent.runtime.material_protocol import STRATEGY, rule_metadata, dates, closure_ready
from ForecastAgent.runtime.collection_actions import pending_passages, review_focus, next_action
from ForecastAgent.runtime.context import collection_context
from ForecastAgent.runtime.contracts import ContractError, validate
from ForecastAgent.tools.registry import COLLECTION_TOOLS

FIXTURE = Path(__file__).parents[1]/'fixtures/materials_v3_saved_controls.json'


def saved(qid):
    return next(c for c in json.loads(FIXTURE.read_text(encoding='utf-8'))['cases'] if c['id']==qid)


def task_for(qid, root, *, modern=True):
    pool, _ = inputs()
    case = next(c for c in pool['cases'] if c['id']==qid)
    request = request_for(case, 'candidate', repair_v2=True)
    if modern:
        request['acquisition_strategy'] = STRATEGY
    task = RetrievalTask(Path(root), request)
    task.bundle['pages'] = copy.deepcopy(case['pages'])
    task.bundle['source_leads'] = copy.deepcopy(case.get('source_leads', {}))
    task.bundle['acquisition_limits'].update(tavily_basic=0, exa_search=0)
    task.search_limit = task.exa_limit = 0
    return task


def plan_for(qid, *, modern=True):
    plan = copy.deepcopy(next(r['arguments'] for r in saved(qid)['calls'] if r['tool']=='plan_evidence'))
    if modern:
        for need in plan['needs']:
            need['rule_time_fields'] = []
    return plan


def tool_call(name, args, ident):
    return {'id':str(ident), 'type':'function', 'function':{'name':name,'arguments':json.dumps(args)}}


def review_state(task, qid):
    row = saved(qid)
    task.bundle['passages'] = copy.deepcopy(row['passages'])
    task.bundle.setdefault('progress', {})['delivery_passages'] = copy.deepcopy(row['delivery_passages'])
    task.bundle['transcript'].extend(copy.deepcopy(row['read_source_transcript']))


class MaterialProtocolTests(TestCase):
    def test_versions_are_explicit_and_release_dependencies_stay_frozen(self):
        pool, _ = inputs()
        request = pool['cases'][0]['request']
        old, _ = prepare(request)
        new, _ = prepare({**request, 'acquisition_strategy':STRATEGY})
        self.assertEqual(old['acquisition_strategy'], 'intelligent_materials_v2')
        self.assertEqual(new['acquisition_strategy'], STRATEGY)
        self.assertGreaterEqual(len(verify_baseline()['initial_frozen_file_sha256']), 47)

    def test_court_queue_stops_forcing_reviews_after_two_batches_without_deleting_material(self):
        with TemporaryDirectory() as root:
            task = task_for('43991', root)
            task.execute('plan_evidence', plan_for('43991'), '')
            review_state(task, '43991')
            before = task.budget()
            originals = copy.deepcopy(task.bundle['pages'])
            for _ in range(2):
                focus = review_focus(task)
                self.assertTrue(focus['mandatory'])
                self.assertLessEqual(len(focus['passages']), 4)
                self.assertLessEqual(sum(len(r['text']) for r in focus['passages']), 6000)
                self.assertEqual(next_action(task)['tool'], 'review_passages')
                args = {'items':[{'passage_id':r['passage_id'], 'action':'keep',
                    'need_ids':r['need_ids'], 'reason':'Retain the exact saved court material for later analysis.'}
                    for r in focus['passages']]}
                validate(task, 'review_passages', args, configure_tools(task,COLLECTION_TOOLS))
                result = task.execute('review_passages', args, '')
                self.assertTrue(all(r['ok'] for r in result['items']))
            self.assertFalse(review_focus(task)['mandatory'])
            self.assertIsNone(next_action(task))
            self.assertGreater(len(pending_passages(task)), 0)
            self.assertEqual(task.budget(), before)
            self.assertEqual(task.bundle['pages'], originals)
            task.save()
            resumed = RetrievalTask(Path(root), task.bundle['request'])
            self.assertFalse(review_focus(resumed)['mandatory'])
            self.assertEqual(resumed.budget(), before)
            self.assertFalse(closure_ready(resumed))

    def test_deferred_span_is_reported_and_can_be_voluntarily_revisited(self):
        with TemporaryDirectory() as root:
            task = task_for('43991', root)
            task.execute('plan_evidence', plan_for('43991'), '')
            review_state(task, '43991')
            row = pending_passages(task)[0]
            args = {'items':[{'passage_id':row['passage_id'], 'action':'defer',
                             'need_ids':[], 'reason':'Optional material needs later reading; no relevance verdict.'}]}
            validate(task, 'review_passages', args, configure_tools(task,COLLECTION_TOOLS))
            task.execute('review_passages', args, '')
            report = terminal_report(task)
            self.assertEqual(report['deferred_passage_count'], 1)
            self.assertFalse(task.bundle['passage_dispositions'][row['passage_id']]['truth_verified'])
            task.execute('review_passages', {'items':[{**args['items'][0], 'action':'keep',
                'need_ids':row['need_ids']}]}, '')
            self.assertEqual(terminal_report(task)['deferred_passage_count'], 0)
            self.assertTrue(task.bundle['excerpts'])

    def test_stale_deferred_span_cannot_be_banked(self):
        with TemporaryDirectory() as root:
            task = task_for('43991', root)
            task.execute('plan_evidence', plan_for('43991'), '')
            review_state(task, '43991')
            row = pending_passages(task)[0]
            task.execute('review_passages', {'items':[{'passage_id':row['passage_id'],
                'action':'defer', 'need_ids':[], 'reason':'Leave uncertain passage for later.'}]}, '')
            task.bundle['pages'][row['url']]['content'] += '\nA different source version.'
            result = task.execute('review_passages', {'items':[{'passage_id':row['passage_id'],
                'action':'keep', 'need_ids':row['need_ids'], 'reason':'Attempt stale span.'}]}, '')
            self.assertFalse(result['items'][0]['ok'])
            self.assertEqual(task.bundle['excerpts'], [])

    def test_stablecoin_saved_trace_exports_after_assessment_without_two_extra_requests(self):
        calls = saved('36871')['calls'][:6]
        with TemporaryDirectory() as root, patch.dict('os.environ', {'EXA_API_KEY':''}):
            task = task_for('36871', root)
            task.save()
            count = []
            def respond(*args, **kwargs):
                row = calls[len(count)]
                count.append(row['tool'])
                arguments = copy.deepcopy(row['arguments'])
                if row['tool']=='plan_evidence':
                    arguments = plan_for('36871')
                if row['tool']=='assess_materials':
                    for item in arguments['items']:
                        item['missing_items'] = []
                return {'tool_calls':[tool_call(row['tool'], arguments, len(count))]}
            with patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=respond) as model, \
                 patch('ForecastAgent.providers.http.download') as network:
                b = run_retrieval(task.bundle['request'], root, '', 'mock-key')
            network.assert_not_called()
            self.assertEqual(model.call_count, 6)
            self.assertEqual(b['result']['agent_declared_gaps'], [])
            self.assertEqual(b['result']['material_adequacy_status'], 'agent_declared_adequate')
            self.assertEqual(b['result']['execution_report']['stop_reason'], 'agent_declared_targets_complete')
            self.assertFalse(b['result']['truth_verified'])
            self.assertTrue(all(r['wire_declaration']['missing_material']=='None'
                                for r in b['material_assessments'].values()))
            text = '\n'.join(e['text'] for e in b['excerpts'])
            self.assertIn('$184,368,461,962.97', text)
            self.assertIn('$73,360,220,193.95', text)
            self.assertEqual(b['searches'], [])
            self.assertEqual(b['fetch_attempts'], [])

    def test_real_gap_is_not_normalized_away_and_batch_is_atomic(self):
        with TemporaryDirectory() as root:
            task = task_for('36871', root)
            task.execute('plan_evidence', plan_for('36871'), '')
            review_state(task, '36871')
            for row in pending_passages(task):
                task.execute('review_passages', {'items':[{'passage_id':row['passage_id'],
                    'action':'keep', 'need_ids':row['need_ids'], 'reason':'Bank saved original rows.'}]}, '')
            args = copy.deepcopy(next(r['arguments'] for r in saved('36871')['calls'] if r['tool']=='assess_materials'))
            for item in args['items']:
                item['missing_items'] = []
            args['items'][-1]['missing_material'] = 'The target observation date remains missing.'
            with self.assertRaises(ContractError):
                assess(task, args)
            self.assertFalse(task.bundle.get('material_assessments'))
            self.assertFalse(task.bundle.get('material_assessment_events'))

    def test_saved_adequate_aliases_are_narrow_not_semantic_gap_parsing(self):
        from ForecastAgent.runtime.material_protocol import normalize_assessment
        for text in ('None', 'No further material needed.', ''):
            item = normalize_assessment({'status':'adequate','missing_items':[], 'missing_material':text}, {})
            self.assertEqual(item['missing_material'], '')
        for text in ('None except missing opening date', 'No material from the target period', 'unknown'):
            with self.assertRaises(ContractError):
                normalize_assessment({'status':'adequate','missing_items':[], 'missing_material':text}, {})
        with self.assertRaises(ContractError):
            normalize_assessment({'status':'partial','missing_items':[], 'missing_material':'None'}, {})

    def test_hack_invented_boundary_is_rejected_without_partial_plan_mutation(self):
        with TemporaryDirectory() as root:
            task = task_for('43501', root)
            plan = plan_for('43501')
            self.assertIn('2026-05-07', dates(plan['needs'][0]['condition']))
            before = copy.deepcopy(plan)
            with self.assertRaises(ContractError) as exc:
                task.execute('plan_evidence', plan, '')
            self.assertEqual(exc.exception.details['code'], 'unsupported_target_date')
            self.assertIsNone(task.bundle['plan'])
            self.assertEqual(plan, before)
            self.assertIsNone(next(r['value'] for r in rule_metadata(task)['fields'] if r['field']=='open_time'))

    def test_unknown_required_opening_survives_context_resume_and_blocks_adequate(self):
        from ForecastAgent.runtime.material_protocol import normalize_assessment
        with TemporaryDirectory() as root:
            task = task_for('43501', root)
            plan = plan_for('43501')
            for need in plan['needs']:
                need['condition'] = 'Incident after the question opening; retain original threshold and deadline rules.'
                need['rule_time_fields'] = ['open_time']
            task.execute('plan_evidence', plan, '')
            before = task.budget()
            task.bundle['messages'] = [{'role':'system','content':'Acquire material only.'}]
            state = json.loads(collection_context(task, max_chars=12000)[1]['content'])
            self.assertEqual(state['immutable_rule_metadata'], rule_metadata(task))
            need = task.bundle['plan'][0]
            with self.assertRaises(ContractError):
                normalize_assessment({'status':'adequate','missing_items':[]}, need)
            task.save()
            restored = RetrievalTask(Path(root), task.bundle['request'])
            self.assertEqual(restored.bundle['plan'][0]['rule_metadata_bindings'][0]['state'], 'unknown')
            self.assertEqual(restored.budget(), before)

    def test_observation_dates_are_allowed_in_query_but_not_invented_rule_boundaries(self):
        with TemporaryDirectory() as root:
            task = task_for('43501', root)
            plan = plan_for('43501')
            for need in plan['needs']:
                need['condition'] = 'Reported incident under original timing and threshold rules.'
                need['query'] = 'Incident update May 7, 2026; an observation lead, not eligibility.'
            task.execute('plan_evidence', plan, '')
            self.assertIn('May 7, 2026', task.bundle['plan'][0]['query'])
            self.assertEqual(dates('May 7, 2026 / 7 May 2026 / 2026-05-07'), {'2026-05-07'})

    def test_known_metadata_dates_bind_without_retyping_and_reject_arbitrary_values(self):
        with TemporaryDirectory() as root:
            task = task_for('43501', root)
            task.bundle['request']['open_time'] = '2026-05-07T14:00:00Z'
            plan = plan_for('43501')
            for need in plan['needs']:
                need['rule_time_fields'] = ['open_time']
            task.execute('plan_evidence', plan, '')
            self.assertEqual(task.bundle['plan'][0]['rule_metadata_bindings'][0]['value'], '2026-05-07T14:00:00Z')
            self.assertEqual(task.bundle['plan'][0]['rule_metadata_bindings'][0]['origin'], 'immutable_request')

    def test_transport_interruption_still_exports_rule_unknowns_and_actual_material_state(self):
        with TemporaryDirectory() as root, patch.dict('os.environ', {'EXA_API_KEY':''}):
            task = task_for('43501', root)
            task.save()
            plan = plan_for('43501')
            for need in plan['needs']:
                need['condition'] = 'Incident under the original timing and threshold criteria.'
                need['rule_time_fields'] = ['open_time']
            replies = [{'tool_calls':[tool_call('plan_evidence', plan, 1)]}, RuntimeError('Service unavailable')]
            with patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=replies), \
                 patch('ForecastAgent.providers.http.download') as network:
                b = run_retrieval(task.bundle['request'], root, '', 'mock-key')
            network.assert_not_called()
            self.assertTrue(b['result']['incomplete'])
            self.assertTrue(b['result']['execution_report']['interrupted'])
            self.assertEqual(b['result']['execution_report']['stop_reason'], 'model_transport_failure')
            self.assertEqual(b['result']['gaps'], [])
            self.assertTrue(b['result']['material_report']['unresolved_material_targets'])
            self.assertEqual(b['result']['material_report']['needs'][0]['unknown_rule_fields'], ['open_time'])
            self.assertFalse(b['result']['acquisition_complete'])

    def test_program_stop_reports_unassessed_targets_without_inventing_material_gaps(self):
        with TemporaryDirectory() as root, patch.dict('os.environ', {'EXA_API_KEY':''}):
            task = task_for('44801', root)
            task.save()
            plan = plan_for('44801')
            for need in plan['needs']:
                need['condition'] = 'AAA national regular gasoline prices under the original event deadline and record threshold.'
            replies = [{'tool_calls':[tool_call('plan_evidence', plan, 1)]}] + [{'tool_calls':[]}] * 3
            with patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=replies) as model, \
                 patch('ForecastAgent.providers.http.download') as network:
                b = run_retrieval(task.bundle['request'], root, '', 'mock-key')
            self.assertEqual(model.call_count, 4)
            network.assert_not_called()
            self.assertEqual(b['result']['agent_declared_gaps'], [])
            self.assertEqual(b['result']['material_adequacy_status'], 'unresolved')
            self.assertEqual(b['result']['execution_report']['stop_reason'], 'stalled')
            self.assertTrue(b['result']['material_report']['unresolved_material_targets'])
            self.assertFalse(b['result']['acquisition_complete'])

    def test_v2_remains_strict_and_does_not_accept_v3_deferral_schema(self):
        with TemporaryDirectory() as root:
            task = task_for('43991', root, modern=False)
            task.execute('plan_evidence', plan_for('43991', modern=False), '')
            review_state(task, '43991')
            self.assertNotIn('mandatory', review_focus(task))
            row = pending_passages(task)[0]
            with self.assertRaises(ContractError):
                validate(task, 'review_passages', {'items':[{'passage_id':row['passage_id'],
                    'action':'defer', 'need_ids':[], 'reason':'Do not silently migrate V2.'}]}, configure_tools(task, COLLECTION_TOOLS))
