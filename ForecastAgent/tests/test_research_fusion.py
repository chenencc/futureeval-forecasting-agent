"""Map-to-action feedback without nested models or refreshed provider budgets."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import POLICY, fusion, fusion_trial, runtime, simple_map, state
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.tools.registry import TOOLS
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_simple_map import literal_proposal


def task(directory):
    b = source_bundle(); request = copy.deepcopy(b['request'])
    request.update(research_state_policy=POLICY, research_acquisition_policy=fusion.POLICY_NAME)
    t = RetrievalTask(Path(directory), request)
    t.bundle['plan'] = [{'id':'baseline', 'priority':'critical', 'condition':'Relevant dated baseline, with exact units.',
                         'expected_source':'Official report', 'query':'Official report dated baseline'}]
    t.bundle['pages'] = b['pages']; fusion.initialize(t); t.save()
    return t


class FusionTests(unittest.TestCase):
    def test_links_are_declared_only_for_opt_in_network_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = task(tmp); tools = runtime.configure(t, TOOLS)
            search = next(e for e in tools if e['function']['name']=='search_tavily')
            update = next(e for e in tools if e['function']['name']=='update_research_state')
            self.assertIn(fusion.LINK_FIELD, search['function']['parameters']['properties'])
            self.assertEqual(update['function']['parameters']['required'], simple_map.MAP_SCHEMA['required'])
            self.assertIn('CONTIGUOUS literal quote',update['function']['parameters']['properties']['nodes']['items']['properties']['claim']['description'])
            self.assertNotIn(fusion.LINK_FIELD, next(e for e in tools if e['function']['name']=='finish_collection')['function']['parameters']['properties'])
            t.bundle['request'][fusion.POLICY_FIELD]='disabled'
            self.assertEqual(fusion.configure(t,TOOLS), TOOLS)

    def test_fusion_guide_replaces_duplicate_legacy_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp); prompt=runtime.guide(t,'Base tool instructions.')
            self.assertIn('Map-guided acquisition',prompt)
            self.assertNotIn('Experimental research-loop policy',prompt)
            self.assertLess(len(fusion.GUIDE),2600)
            t.bundle['request'][fusion.POLICY_FIELD]='disabled'
            self.assertIn('Experimental research-loop policy',runtime.guide(t,'Base tool instructions.'))

    def test_actual_action_consumes_existing_budget_once_and_updates_frontier(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp); ident=fusion.targets(t)[0]['id']
            def native(name,args,key):
                self.assertNotIn(fusion.LINK_FIELD,args)
                t.bundle['searches'].append({'status':'completed', 'results':[]})
                t.bundle['pages']['https://example.org/new']={'content':'A dated official finding.'*30}
                return {'ok':True}
            result=fusion.execute(t,'search_tavily',{'query':'official baseline',fusion.LINK_FIELD:[ident]},'test',native)
            self.assertEqual(len(t.bundle['searches']),1)
            self.assertTrue(result['research_frontier']['pending_map_update'])
            event=t.bundle['research_acquisition']['events'][0]
            self.assertEqual(event['research_node_ids'],[ident])
            self.assertEqual(len(event['new_bodies']),1)
            self.assertEqual(event['new_bodies'][0]['body_sha256'], hashlib.sha256(('A dated official finding.'*30).encode()).hexdigest())
            self.assertEqual(event['budget_before']['tavily_basic_remaining']-event['budget_after']['tavily_basic_remaining'],1)
            fusion.initialize(t)

    def test_known_id_set_shape_is_normalized_without_inventing_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);ident=fusion.targets(t)[0]['id'];seen=[]
            def native(name,args,key):
                seen.append(args);t.bundle['searches'].append({'status':'completed','results':[]})
                return {'ok':True}
            fusion.execute(t,'search_tavily',{'query':'baseline','need_ids':'baseline',fusion.LINK_FIELD:[ident,ident]},'test',native)
            self.assertEqual(seen,[{'query':'baseline','need_ids':['baseline']}])
            event=t.bundle['research_acquisition']['events'][0]
            self.assertEqual(event['research_node_ids'],[ident])
            self.assertEqual(len(event['argument_normalizations']),2)
            self.assertEqual(len(t.bundle['searches']),1)
            result,changes=fusion.normalize_need_ids(t,{'need_ids':'UNKNOWN'})
            self.assertEqual(result,{'need_ids':'UNKNOWN'});self.assertEqual(changes,[])
            available=runtime.filter_tools(t,runtime.configure(t,TOOLS))
            search=next(e for e in available if e['function']['name']=='search_tavily')['function']['parameters']['properties']
            self.assertEqual(search['need_ids']['items']['enum'],['baseline'])
            self.assertEqual(search[fusion.LINK_FIELD]['items']['enum'],[ident])

    def test_blocked_body_does_not_clear_no_progress_or_request_map_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp); ident=fusion.targets(t)[0]['id']
            def native(*args):
                t.bundle['pages']['https://example.org/blocked']={'content':'Just a moment. Verify you are human.'}
                return {'ok':True}
            result=fusion.execute(t,'fetch_page',{'url':'https://example.org/blocked',fusion.LINK_FIELD:[ident]},'test',native)
            frontier=result['research_frontier']
            self.assertFalse(frontier['pending_map_update'])
            self.assertEqual(frontier['no_readable_body_by_node'][ident],1)
            self.assertFalse(t.bundle['research_acquisition']['events'][0]['new_bodies'][0]['usable_text'])

    def test_trial_resume_uses_remaining_model_caps_and_restores_globals(self):
        from ForecastAgent.runtime import retrieval
        before=(retrieval.COLLECTION_MAX_TURNS,retrieval.COLLECTION_HTTP_PER_DISPATCH,retrieval.MODEL_FAILURES_PER_DISPATCH,retrieval.MAX_RUN_SECONDS)
        with fusion_trial.bounded_dispatch(7,6,1,23):
            self.assertEqual(retrieval.COLLECTION_MAX_TURNS,6)
            self.assertEqual(retrieval.COLLECTION_HTTP_PER_DISPATCH,9)
            self.assertEqual(retrieval.MODEL_FAILURES_PER_DISPATCH,1)
            self.assertEqual(retrieval.MAX_RUN_SECONDS,23)
        self.assertEqual((retrieval.COLLECTION_MAX_TURNS,retrieval.COLLECTION_HTTP_PER_DISPATCH,retrieval.MODEL_FAILURES_PER_DISPATCH,retrieval.MAX_RUN_SECONDS),before)

    def test_invalid_node_cannot_trigger_network_or_modify_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp); before=copy.deepcopy(t.bundle)
            with self.assertRaisesRegex(ValueError,'existing research_frontier'):
                fusion.execute(t,'search_tavily',{'query':'q',fusion.LINK_FIELD:['missing']},'test',lambda *a:self.fail('Network forbidden'))
            self.assertEqual(t.bundle,before)

    def test_retagging_cannot_repeat_failed_action_or_renew_search_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp); ident=fusion.targets(t)[0]['id']
            def native(*args):
                t.bundle['searches'].append({'status':'failed', 'results':[]}); raise RuntimeError('Service failure')
            with self.assertRaises(RuntimeError): fusion.execute(t,'search_tavily',{'query':'q',fusion.LINK_FIELD:[ident]},'test',native)
            with self.assertRaisesRegex(ValueError,'already reserved'):
                fusion.execute(t,'search_tavily',{'query':'q'},'test',lambda *a:self.fail('Duplicate network forbidden'))
            self.assertEqual(len(t.bundle['searches']),1)
            self.assertEqual(t.bundle['research_acquisition']['events'][0]['status'],'failed')
            resumed=RetrievalTask(Path(tmp),t.bundle['request']); fusion.initialize(resumed)
            self.assertEqual(len(resumed.bundle['searches']),1)

    def test_nested_batch_is_one_intent_with_native_child_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp); ident=fusion.targets(t)[0]['id']
            def child(name,args,key):
                t.bundle['fetch_attempts'].append({'status':'received'}); return {'ok':True}
            def native(name,args,key):
                fusion.execute(t,'fetch_page',{'url':'https://example.org/report'},key,child)
                return {'ok':True}
            fusion.execute(t,'read_sources',{'urls':['https://example.org/report'],fusion.LINK_FIELD:[ident]},'test',native)
            self.assertEqual(len(t.bundle['research_acquisition']['events']),1)
            self.assertEqual(len(t.bundle['fetch_attempts']),1)

    def test_source_bound_map_updates_pending_flag_without_network_or_new_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp); t.bundle['research_acquisition']['pending_map_update']=True
            proposed=literal_proposal(t.bundle); proposed['relations']=[]
            # Legacy fixture's driver is an unbound mechanism in the source-first contract.
            proposed['nodes'][1]['evidence_ids']=[]
            before=t.budget();planning=fusion.planning_snapshot(t)
            result=t.execute('update_research_state',proposed,'test')
            self.assertEqual(result['revision'],1)
            self.assertFalse(result['research_frontier']['pending_map_update'])
            self.assertEqual(t.budget(),before)
            self.assertTrue(fusion.planning_advanced(t,planning))
            previous=fusion.planning_snapshot(t)
            t.bundle['research_loop']['revision']+=1
            self.assertFalse(fusion.planning_advanced(t,previous))
            t.bundle['research_loop']['revision']-=1
            self.assertEqual(t.bundle['research_loop']['events'][-1]['acceptance']['map_protocol'],simple_map.PROTOCOL)

    def test_source_suggestions_do_not_force_out_map_tools_or_override_hard_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp)
            self.assertIsNone(fusion.advisory_forcing(t,'read_sources'))
            for name in ('plan_evidence','search_exa','finish_collection'):
                self.assertEqual(fusion.advisory_forcing(t,name),name)
            t.bundle['request'][fusion.POLICY_FIELD]='disabled'
            self.assertEqual(fusion.advisory_forcing(t,'read_sources'),'read_sources')

    def test_first_map_bootstrap_is_bounded_and_failures_do_not_restart_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);t.bundle['research_acquisition']['pending_map_update']=True
            self.assertEqual(fusion.advisory_forcing(t,'read_sources'),'inspect_research_state')
            t.execute('inspect_research_state',{'query':'report','limit':3},'test')
            self.assertEqual(fusion.advisory_forcing(t,None),'update_research_state')
            proposal=literal_proposal(t.bundle);proposal['expected_revision']=9
            with self.assertRaisesRegex(ValueError,'Stale research revision'):
                t.execute('update_research_state',proposal,'test')
            self.assertEqual(t.bundle['research_acquisition']['bootstrap_steps'],2)
            t.bundle['research_acquisition']['bootstrap_steps']=4
            self.assertIsNone(fusion.advisory_forcing(t,'read_sources'))
            self.assertEqual(fusion.advisory_forcing(t,'finish_collection'),'finish_collection')

    def test_map_output_has_room_for_arguments_and_other_tools_keep_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);options=fusion.model_options(t,'update_research_state')
            self.assertGreater(options['max_output_tokens']-options['reasoning']['max_tokens'],3000)
            self.assertEqual(fusion.model_options(t,'search_tavily'),
                             {'max_output_tokens':3000,'reasoning':{'max_tokens':1200}})
            t.bundle['request'][fusion.POLICY_FIELD]='disabled'
            self.assertEqual(fusion.model_options(t,'update_research_state'),{})

    def test_raw_capture_schema_omits_unused_paragraph_arguments(self):
        from ForecastAgent.tools.registry import COLLECTION_TOOLS
        from ForecastAgent.runtime.contracts import check_schema
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);t.raw_recall=True
            tools=runtime.configure(t,COLLECTION_TOOLS)
            schema=next(e for e in tools if e['function']['name']=='read_sources')['function']['parameters']
            self.assertEqual(schema['required'],['urls'])
            self.assertNotIn('queries',schema['properties'])
            check_schema({'urls':['https://example.org/report']},schema)
            t.bundle['request'][fusion.POLICY_FIELD]='disabled'
            original=next(e for e in fusion.configure(t,COLLECTION_TOOLS) if e['function']['name']=='read_sources')['function']['parameters']
            self.assertIn('queries',original['properties'])

    def test_pending_local_cycle_drains_before_soft_stop_and_cannot_repeat_forever(self):
        from ForecastAgent.runtime.collection_actions import raw_stop_reason
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);t.raw_recall=True
            t.bundle['research_acquisition']['pending_map_update']=True
            t.bundle['search_policy']['exa']='optional'
            t.bundle['control']['no_progress_turns']=3
            self.assertIsNone(raw_stop_reason(t))
            for _ in range(4):
                t.execute('inspect_research_state',{'limit':1},'test')
            self.assertIsNone(fusion.local_cycle(t))
            self.assertEqual(raw_stop_reason(t),'raw_no_progress_limit')
            resumed=RetrievalTask(Path(tmp),t.bundle['request'])
            self.assertIsNone(fusion.local_cycle(resumed))
            self.assertEqual(len(t.bundle.get('model_attempts',[])),0)
            self.assertEqual(len(t.bundle['fetch_attempts']),0)

    def test_later_body_batch_gets_a_bounded_second_map_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp)
            proposed=literal_proposal(t.bundle);proposed['relations']=[]
            proposed['nodes'][1]['evidence_ids']=[]
            t.execute('update_research_state',proposed,'test')
            self.assertEqual(t.bundle['research_loop']['revision'],1)
            self.assertIsNone(fusion.local_cycle(t))
            def native(*args):
                t.bundle['pages']['https://example.org/new']={'content':'New official findings with specific dated figures. '*30}
                return {'ok':True}
            fusion.execute(t,'fetch_page',{'url':'https://example.org/new'},'test',native)
            self.assertEqual(fusion.advisory_forcing(t,None),'inspect_research_state')
            t.execute('inspect_research_state',{'query':'New official','limit':1},'test')
            self.assertEqual(fusion.advisory_forcing(t,None),'update_research_state')
            self.assertEqual(fusion.advisory_forcing(t,'finish_collection'),'finish_collection')
            t.bundle['research_loop']['revision']=state.MAX_UPDATES
            self.assertIsNone(fusion.local_cycle(t))

    def test_empty_assistant_reply_does_not_evict_inspected_source_delivery(self):
        from ForecastAgent.runtime.context import collection_context
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);result=t.execute('inspect_research_state',{'limit':3},'test')
            t.bundle['messages']=[{'role':'system','content':'Base acquisition instructions.'},
                {'role':'assistant','content':None,'tool_calls':[{'id':'read','type':'function','function':{'name':'inspect_research_state','arguments':'{}'}}]},
                {'role':'tool','tool_call_id':'read','content':json.dumps(result)},
                {'role':'assistant','content':None},
                {'role':'assistant','content':None},
                {'role':'assistant','content':None}]
            context=collection_context(t)
            self.assertTrue(any(m.get('tool_call_id')=='read' for m in context))
            model_state=json.loads(context[1]['content'])
            self.assertEqual(model_state['research_binding_frame']['material_sha256'],result['material_sha256'])
            self.assertGreater(model_state['research_binding_frame']['readable_saved_sources'],0)
            self.assertEqual(model_state['research_binding_frame']['inspected_reference_handles'],[r['evidence_id'] for r in result['evidence']])
            available=runtime.filter_tools(t,runtime.configure(t,TOOLS))
            schema=next(e for e in available if e['function']['name']=='update_research_state')['function']['parameters']
            self.assertEqual(schema['properties']['material_sha256']['enum'],[result['material_sha256']])

    def test_resume_rehydrates_previously_inspected_exact_text_without_provider_calls(self):
        from ForecastAgent.runtime.context import collection_context,encode
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);result=t.execute('inspect_research_state',{'limit':3},'test')
            t.bundle['research_acquisition'].pop('inspected_references')
            t.bundle['research_acquisition']['pending_map_update']=True
            t.bundle['messages']=[{'role':'system','content':'Base acquisition instructions.'},
                {'role':'assistant','content':None,'tool_calls':[{'id':'read','type':'function','function':{'name':'inspect_research_state','arguments':'{}'}}]},
                {'role':'tool','tool_call_id':'read','content':json.dumps({'data':result})},
                {'role':'assistant','content':None,'tool_calls':[{'id':'failed','type':'function','function':{'name':'inspect_research_state','arguments':'{}'}}]},
                {'role':'tool','tool_call_id':'failed','content':json.dumps({'ok':False,'data':None})},
                {'role':'assistant','content':None}]
            t.bundle['control']['dispatch_message_start']=5
            before=copy.deepcopy(t.bundle['pages'])
            context=collection_context(t)
            frame=json.loads(context[1]['content'])['research_binding_frame']
            self.assertEqual(frame['inspected_evidence'],[{k:r[k] for k in
                ('evidence_id','url','body_sha256','start','end','text')} for r in result['evidence']])
            self.assertEqual(t.bundle['pages'],before)
            self.assertLessEqual(len(encode(context)),28000)
            for page in t.bundle['pages'].values():page['content']+='New source version.'
            self.assertEqual(fusion.binding_frame(t)['inspected_evidence'],[])

    def test_large_catalog_preserves_exact_spans_with_explicit_omission_when_needed(self):
        from ForecastAgent.research_loop import grounding
        from ForecastAgent.runtime.context import collection_context,encode
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);t.bundle['request'][grounding.FIELD]=grounding.POLICY
            for i in range(12):
                t.bundle['pages']['https://example.org/background/'+str(i)]={'content':'An independent dated report gives background observations. '*20}
            t.bundle['research_acquisition']['pending_map_update']=True
            reply=t.execute('inspect_research_state',{'limit':1},'test')
            t.bundle['messages']=[{'role':'system','content':'Preserve immutable collection instructions. '*220},
                {'role':'assistant','content':None,'tool_calls':[{'id':'read','type':'function','function':{'name':'inspect_research_state','arguments':'{"limit":1}'}}]},
                {'role':'tool','tool_call_id':'read','content':json.dumps(reply)}]
            original=fusion.binding_frame(t)
            delivered=collection_context(t,max_chars=18000)
            frame=json.loads(delivered[1]['content'])['research_binding_frame']
            self.assertTrue('source_catalog' in frame or frame.get('source_catalog_omitted'))
            self.assertEqual(frame['inspected_evidence'],original['inspected_evidence'])
            self.assertEqual(frame['source_context'],original['source_context'])
            self.assertLessEqual(len(encode(delivered)),18000)

    def test_pinned_inspection_deduplicates_only_identical_complete_readings(self):
        from ForecastAgent.runtime.context import pinned_inspection
        ref={'evidence_id':'Rexact','text':'Literal body','url':'https://example.org','start':0,'end':12,'body_sha256':'fixed'}
        payload={'data':{'material_sha256':'material','evidence':[ref], 'source_context':[],
            'source_catalog':{'total_sources':8},'research_state':{'revision':1}}}
        frame={'pending_map_update':True,'material_sha256':'material','inspected_evidence':[ref],'source_context':[]}
        before=copy.deepcopy(payload)
        projected=pinned_inspection(payload,frame)
        self.assertEqual(projected['data']['evidence_handles'],['Rexact'])
        self.assertEqual(projected['data']['research_state'],{'revision':1})
        self.assertEqual(payload,before)
        payload['data']['evidence'][0]['source_id']='https://example.org'
        self.assertEqual(pinned_inspection(payload,frame)['data']['evidence_handles'],['Rexact'])
        changed=copy.deepcopy(frame);changed['inspected_evidence'][0]['text']='Changed'
        self.assertEqual(pinned_inspection(payload,changed),payload)
        changed=copy.deepcopy(frame);changed['pending_map_update']=False
        self.assertEqual(pinned_inspection(payload,changed),payload)

    def test_paired_inputs_pin_raw_collection_in_both_arms(self):
        for arm in ('baseline','fusion'):
            request=fusion_trial.input_for(source_bundle(),'2026-10-09T06:00:00+00:00',arm)
            self.assertEqual(request['acquisition_strategy'],fusion_trial.pipeline.V3_STRATEGY)
            prepared,_=fusion_trial.pipeline.prepare(request)
            self.assertEqual(prepared['acquisition_focus'],'raw_recall')
            with tempfile.TemporaryDirectory() as tmp:
                t=RetrievalTask(Path(tmp),prepared)
                self.assertTrue(t.raw_recall)

    def test_corrupted_journal_and_changed_rules_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);fusion.execute(t,'search_tavily',{'query':'q'},'test',lambda *a:{'ok':True})
            t.bundle['research_acquisition']['events'][0]['arguments']['query']='changed'
            with self.assertRaisesRegex(ValueError,'checksum'):fusion.initialize(t)
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);t.bundle['request']['resolution_criteria']='Changed rules'
            with self.assertRaisesRegex(ValueError,'rules changed'):fusion.initialize(t)

    def test_each_table_row_and_literal_observation_remains_a_source_coordinate(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);body='## Dated official data\n\n| Date | Price |\n| 2026-10-06 | 12374.26 |\n'+'Background data. '*90
            t.bundle['pages']['https://example.org/report']['content']=body
            material=state.catalog(t.bundle)
            row=next(r for r in material['spans'].values() if '12374.26' in r['text'])
            self.assertEqual(row['text'],'| 2026-10-06 | 12374.26 |\n')
            for r in material['spans'].values():self.assertEqual(body[r['start']:r['end']],r['text'])


if __name__=='__main__':unittest.main()
