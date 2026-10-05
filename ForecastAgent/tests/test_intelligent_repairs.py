"""Offline mechanics against saved failures; no claim of new model quality."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition.frontier_compare import inputs, request_for
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.intelligent_acquisition import (
    CURRENT_STRATEGY, STRATEGY, question_handles, validate_plan, configure_tools)
from ForecastAgent.runtime.contracts import ContractError
from ForecastAgent.runtime.context import collection_context, encode
from ForecastAgent.runtime.delivery import acknowledge, stage_read_passages
from ForecastAgent.runtime.collection_actions import next_action, pending_passages, review_focus
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.tests.test_delivery import group
from ForecastAgent.tests.test_intelligent_acquisition import REQUEST, PLAN, source, URL

FIXTURE = Path(__file__).parents[1]/'fixtures/intelligent_repair_responses.json'


def saved_cases():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))['cases']


def replay_task(case, directory):
    request = request_for(case, 'candidate')
    request['acquisition_strategy'] = CURRENT_STRATEGY
    task = RetrievalTask(Path(directory), request)
    task.bundle['pages'] = copy.deepcopy(case['pages'])
    return task


def adapt_saved_plan(task, plan):
    """Test-only wire translation, explicitly NOT a new agent decision.

    Bind available original fields. An empty old field may be repaired only when
    its exact saved quote matches one unique other field; never invent a rule.
    """
    plan = copy.deepcopy(plan)
    handles = {r['field']:r['id'] for r in question_handles(task)}
    for need in plan['needs']:
        refs = []
        for span in need.pop('question_spans', []):
            field = span.get('field')
            if field not in handles:
                matching = [f for f in handles if span.get('quote') and
                            span['quote'] in task.bundle['request'][f]]
                if len(matching) != 1:
                    raise ValueError('Test adapter cannot infer a field')
                field = matching[0]
            refs.append(handles[field])
        need['question_refs'] = list(dict.fromkeys(refs))
    return plan


class IntelligentRepairTests(TestCase):
    def test_field_handles_preserve_long_original_and_validate_atomically(self):
        with TemporaryDirectory() as root:
            request = {**REQUEST, 'pipeline':'collection', 'acquisition_profile':'collection_v3',
                       'acquisition_strategy':CURRENT_STRATEGY,
                       'resolution_criteria':REQUEST['resolution_criteria']*100}
            task = RetrievalTask(Path(root), request)
            original = copy.deepcopy(task.bundle['request'])
            handles = question_handles(task)
            ref = next(r['id'] for r in handles if r['field']=='resolution_criteria')
            plan = copy.deepcopy(PLAN)
            plan['needs'][0]['question_refs'] = [ref]
            bad = copy.deepcopy(plan['needs'])
            bad.append({**copy.deepcopy(bad[0]), 'id':'bad', 'question_refs':['forged']})
            with self.assertRaises(ContractError):
                validate_plan(task, bad)
            self.assertNotIn('question_spans', bad[0])
            task.execute('plan_evidence', plan, '')
            span = task.bundle['plan'][0]['question_spans'][0]
            self.assertEqual(span['quote'], original['resolution_criteria'])
            self.assertEqual(span['end_char'], len(span['quote']))
            self.assertEqual(task.bundle['request'], original)
            self.assertEqual(len(handles), 3)
            task.bundle['messages'] = [{'role':'system','content':'Acquire original source material only.'}]
            task.bundle['pages'] = {f'https://example.org/{i}':source('Long inventory context '*300) for i in range(20)}
            view = collection_context(task,max_chars=14000)
            state = json.loads(view[1]['content'])
            self.assertEqual(state['task_protocol']['question']['resolution_criteria'], original['resolution_criteria'])
            self.assertEqual(state['original_question_handles'],handles)
            self.assertLessEqual(len(encode(view)),14000)

    def test_first_projected_read_forces_review_in_the_same_model_request(self):
        pool,_ = inputs()
        case = next(c for c in pool['cases'] if c['id']=='36871')
        saved = next(c for c in saved_cases() if c['id']=='36871')
        old_plan = next(r['arguments'] for r in saved['calls'] if r['tool']=='plan_evidence')
        read = next(r['arguments'] for r in saved['calls'] if r['tool']=='read_document')
        with TemporaryDirectory() as root, patch.dict('os.environ',{'EXA_API_KEY':''}):
            task = replay_task(case,root)
            task.save()
            plan = adapt_saved_plan(task,old_plan)
            count = []
            def call(name,args,ident):
                return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}
            def respond(messages,*args,**kwargs):
                count.append(kwargs.get('forced_tool'))
                if len(count)==1:
                    return {'tool_calls':[call('plan_evidence',plan,'plan')]}
                if len(count)==2:
                    return {'tool_calls':[call('read_document',read,'read')]}
                self.assertEqual(kwargs['forced_tool'],'review_passages')
                focus=next(json.loads(m['content']) for m in messages if m['role']=='user'
                    and json.loads(m['content']).get('kind')=='pending_material_review')
                return {'tool_calls':[call('review_passages',{'items':[
                    {'passage_id':r['passage_id'],'action':'keep','need_ids':[plan['needs'][0]['id']],
                     'reason':'Keep the dated original rows and all table labels.'} for r in focus['passages']]},'review'),
                    call('finish_collection',{'gaps':['Other target materials remain unassessed.']},'finish')]}
            with patch('ForecastAgent.runtime.retrieval.ask_ultra',side_effect=respond) as model, \
                 patch('ForecastAgent.providers.http.download') as fetch:
                bundle=run_retrieval(task.bundle['request'],root,'','mock-key')
            self.assertEqual(model.call_count,3)
            fetch.assert_not_called()
            text=''.join(e['text'] for e in bundle['excerpts'])
            self.assertIn('$184,368,461,962.97',text)
            self.assertIn('$73,360,220,193.95',text)
            self.assertFalse(bundle['submitted_to_metaculus'])

    def test_saved_five_plans_translate_without_semantic_edits(self):
        pool, _ = inputs()
        for saved in saved_cases():
            case = next(c for c in pool['cases'] if c['id']==saved['id'])
            with self.subTest(question_id=case['id']), TemporaryDirectory() as root:
                task = replay_task(case, root)
                valid = next(r['arguments'] for r in saved['calls'] if r['tool']=='plan_evidence'
                             and isinstance(r['arguments'],dict) and isinstance(r['arguments'].get('needs'),list))
                args = adapt_saved_plan(task, valid)
                before = task.budget()
                task.execute('plan_evidence', args, '')
                self.assertEqual([n['condition'] for n in task.bundle['plan']],
                                 [n['condition'] for n in valid['needs']])
                self.assertEqual(task.budget(), before)
                for need in task.bundle['plan']:
                    for span in need['question_spans']:
                        self.assertEqual(span['quote'], case['request'][span['field']])

    def test_legacy_wrong_field_discloses_exact_path_and_matching_field(self):
        with TemporaryDirectory() as root:
            task = RetrievalTask(Path(root), {**REQUEST, 'pipeline':'collection',
                'acquisition_profile':'collection_v3','acquisition_strategy':STRATEGY})
            needs = [{'question_spans':[{'field':'fine_print','quote':'agency June table'}]}]
            with self.assertRaises(ContractError) as error:
                validate_plan(task, needs)
            self.assertEqual(error.exception.details['field'],'needs[0].question_spans[0]')
            self.assertEqual(error.exception.details['allowed_values'],['resolution_criteria'])

    def test_actual_saved_failed_responses_stop_after_one_repair_without_network(self):
        pool, _ = inputs()
        for qid in ('43494','44801'):
            case = next(c for c in pool['cases'] if c['id']==qid)
            saved = next(c for c in saved_cases() if c['id']==qid)
            calls = [r for r in saved['calls'] if r['tool']=='plan_evidence'][:2]
            messages = [{'tool_calls':[{'id':str(i),'type':'function','function':
                {'name':'plan_evidence','arguments':json.dumps(r['arguments'])}}]} for i,r in enumerate(calls)]
            with self.subTest(question_id=qid), TemporaryDirectory() as root, \
                 patch.dict('os.environ',{'EXA_API_KEY':''}), \
                 patch('ForecastAgent.runtime.retrieval.ask_ultra',side_effect=messages) as model, \
                 patch('ForecastAgent.providers.http.download') as fetch:
                task = replay_task(case, root)
                task.bundle['searches'] = [{'status':'reserved','results':[]}]*3
                task.save()
                before = task.budget()
                result = run_retrieval(task.bundle['request'], root, '', 'mock-key')
                self.assertEqual(model.call_count,2)
                fetch.assert_not_called()
                self.assertEqual(result['result']['termination_reason'],'plan_repair_limit')
                self.assertTrue(result['result']['material_report']['plan_missing'])
                self.assertFalse(result['result']['acquisition_complete'])
                restored = RetrievalTask(Path(root), task.bundle['request'])
                self.assertEqual(restored.budget(), before)
                self.assertEqual(len(restored.bundle['control']['material_plan_failures']),2)
                with patch('ForecastAgent.runtime.retrieval.ask_ultra') as cached:
                    run_retrieval(task.bundle['request'], root, '', 'mock-key')
                    cached.assert_not_called()

    def test_actual_stablecoin_table_survives_catalog_eviction_and_banks_exactly(self):
        pool,_ = inputs()
        case = next(c for c in pool['cases'] if c['id']=='36871')
        saved = next(c for c in saved_cases() if c['id']=='36871')
        plan = next(r['arguments'] for r in saved['calls'] if r['tool']=='plan_evidence')
        read = next(r['arguments'] for r in saved['calls'] if r['tool']=='read_document')
        with TemporaryDirectory() as root:
            task = replay_task(case, root)
            task.execute('plan_evidence', adapt_saved_plan(task,plan), '')
            before = task.budget()
            result = task.execute('read_document',read,'')
            group(task,read,result)
            acknowledge(task, collection_context(task))
            # Reproduce later catalog turns which previously evicted both values.
            for i in range(3):
                task.bundle['messages'] += [
                    {'role':'assistant','tool_calls':[{'id':f'catalog{i}','type':'function',
                     'function':{'name':'list_documents','arguments':json.dumps({'url':read['url']})}}]},
                    {'role':'tool','tool_call_id':f'catalog{i}','content':json.dumps({'data':{'catalog':'neutral inventory '*300}})}]
            view = collection_context(task,max_chars=18000)
            focus = next(json.loads(m['content']) for m in view if m['role']=='user'
                         and json.loads(m['content']).get('kind')=='pending_material_review')
            text = ''.join(r['text'] for r in focus['passages'])
            self.assertIn('$184,368,461,962.97',text)
            self.assertIn('$73,360,220,193.95',text)
            self.assertLessEqual(len(encode(view)),18000)
            self.assertEqual(next_action(task)['tool'],'review_passages')
            tools = active_tools(task,configure_tools(task,COLLECTION_TOOLS),'review_passages')
            self.assertEqual({t['function']['name'] for t in tools},
                             {'review_passages','assess_materials','finish_collection'})
            task.execute('review_passages', {'items':[{'passage_id':r['passage_id'],'action':'keep',
                'reason':'Preserve both exact dated market-cap rows with table context.',
                'need_ids':[task.bundle['plan'][0]['id']]} for r in focus['passages']]},'')
            self.assertEqual(''.join(e['text'] for e in task.bundle['excerpts']),text)
            self.assertIsNone(review_focus(task))
            self.assertEqual(task.budget(),before)

    def test_later_call_in_same_failed_batch_cannot_reopen_planning(self):
        pool,_=inputs()
        case=next(c for c in pool['cases'] if c['id']=='43494')
        saved=next(c for c in saved_cases() if c['id']=='43494')
        old_calls=[r['arguments'] for r in saved['calls'] if r['tool']=='plan_evidence'][:2]
        with TemporaryDirectory() as root, patch.dict('os.environ',{'EXA_API_KEY':''}):
            task=replay_task(case,root)
            task.save()
            valid=adapt_saved_plan(task,old_calls[0])
            calls=[{'id':str(i),'type':'function','function':{'name':'plan_evidence',
                    'arguments':json.dumps(args)}} for i,args in enumerate(old_calls+[valid])]
            with patch('ForecastAgent.runtime.retrieval.ask_ultra',return_value={'tool_calls':calls}) as model:
                bundle=run_retrieval(task.bundle['request'],root,'','mock-key')
            self.assertEqual(model.call_count,1)
            self.assertIsNone(bundle['plan'])
            self.assertEqual(len(bundle['control']['material_plan_failures']),2)
            last=bundle['transcript'][2]['result']['data']['contract_error']
            self.assertEqual(last['code'],'program_closing')
            self.assertFalse(bundle['result']['acquisition_complete'])

    def test_focus_bounded_rejectable_stale_and_no_silent_strategy_migration(self):
        from ForecastAgent.acquisition.pipeline import prepare
        with TemporaryDirectory() as root:
            request,_ = prepare(REQUEST)
            task = RetrievalTask(Path(root),request)
            task.execute('plan_evidence',copy.deepcopy(PLAN),'')
            task.bundle['pages'][URL] = source('Official source table rows and definitions.\n'*500)
            text=task.bundle['pages'][URL]['content']
            stage_read_passages(task,{'content':text,'start_char':0,'end_char':len(text)}, {'url':URL})
            focus = review_focus(task)
            self.assertLessEqual(sum(len(r['text']) for r in focus['passages']),6000)
            self.assertLessEqual(len(focus['passages']),2)
            first=focus['passages'][0]
            task.execute('review_passages',{'items':[{'passage_id':first['passage_id'],'action':'reject',
                'need_ids':[],'reason':'Generic duplicate background material, not the target row.'}]},'')
            self.assertFalse(task.bundle['excerpts'])
            self.assertNotIn(first['passage_id'],[r['passage_id'] for r in review_focus(task)['passages']])
            task.bundle['pages'][URL]['content'] += ' Revised source.'
            self.assertIsNone(review_focus(task))
            task.save()
            with self.assertRaisesRegex(ValueError,'mismatch|differ|same task'):
                RetrievalTask(Path(root),{**request,'acquisition_strategy':STRATEGY})

    def test_forced_closure_ignores_pending_review_and_exports_remaining_count(self):
        from ForecastAgent.acquisition.pipeline import prepare
        with TemporaryDirectory() as root, patch.dict('os.environ',{'EXA_API_KEY':''}):
            request,_=prepare(REQUEST)
            task=RetrievalTask(Path(root),request)
            task.execute('plan_evidence',copy.deepcopy(PLAN),'')
            task.bundle['pages'][URL]=source()
            text=task.bundle['pages'][URL]['content']
            stage_read_passages(task,{'content':text,'start_char':0,'end_char':len(text)}, {'url':URL})
            task.bundle['control']['forced_close']=True
            task.save()
            with patch('ForecastAgent.runtime.retrieval.ask_ultra') as model:
                bundle=run_retrieval(request,root,'','mock-key')
            model.assert_not_called()
            self.assertFalse(bundle['result']['acquisition_complete'])
            self.assertGreater(bundle['result']['material_report']['pending_passage_count'],0)
            self.assertTrue(any('unreviewed' in g for g in bundle['result']['gaps']))
