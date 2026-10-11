"""Original-preserving views, bounded delivery and atomic graph patches."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from ForecastAgent.research_loop import reading_views, delta, delivery, state, fusion
from ForecastAgent.runtime.context import collection_context, encode
from ForecastAgent.tests.test_research_fusion import task
from ForecastAgent.tests.test_research_grounding import enable
from ForecastAgent.tests.test_research_simple_map import literal_proposal


class SnapshotRepairTests(unittest.TestCase):
    def test_json_view_binds_decoded_posts_to_preserved_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['request'][reading_views.FIELD]=reading_views.POLICY
            body=json.dumps({'post_stream':{'posts':[{'cooked':'<p>Measured value is 16.</p>',
                'created_at':'2026-10-02','username':'Reporter','id':7},{'cooked':'','id':8}]},'ignored':'metadata'*500})
            url='https://example.org/topic';t.bundle['pages']={url:{'content':body}}
            before=copy.deepcopy(t.bundle['pages']);m=state.catalog(t.bundle)
            spans=list(m['spans'].values());self.assertIn('Measured value is 16.',str(spans))
            self.assertNotIn('metadatametadata',str(spans))
            self.assertEqual(t.bundle['pages'],before)
            for s in spans:
                self.assertEqual(s['coordinate_space'],'decoded_post_view')
                self.assertEqual(s['body_sha256'],hashlib.sha256(body.encode()).hexdigest())
                self.assertTrue(s['json_provenance'])
            old=list(m['spans']);t.bundle['pages'][url]['content']=body.replace('16.','17.')
            self.assertFalse(set(old)&set(state.catalog(t.bundle)['spans']))

    def test_inline_encoding_is_omitted_without_changing_original_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);t.bundle['request'][reading_views.FIELD]=reading_views.POLICY
            url='https://example.org/report';body='# Report\n![logo](data:image/png;base64,'+'A'*60000+')\nThe measured count is 16.\n'
            t.bundle['pages']={url:{'content':body}};m=state.catalog(t.bundle)
            self.assertLess(sum(len(s['text']) for s in m['spans'].values()),100)
            for s in m['spans'].values():self.assertEqual(s['text'],body[s['start']:s['end']])
            self.assertEqual(t.bundle['pages'][url]['content'],body)

    def test_restricted_windows_never_enable_whole_json_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);t.bundle['request'][reading_views.FIELD]=reading_views.POLICY
            url='https://example.org/report';body='visible sentence. '+('hidden '*80)
            t.bundle['pages']={url:{'content':body}}
            t.bundle['research_map_visible_references']=[{'url':url,'body_sha256':hashlib.sha256(body.encode()).hexdigest(),'start':0,'end':17}]
            m=state.catalog(t.bundle);self.assertNotIn('hidden',str(m['spans']))

    def test_patch_retains_omitted_nodes_and_rejects_implicit_replace_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);p=literal_proposal(t.bundle);t.execute('update_research_state',p,'first')
            t.bundle['request'][delta.FIELD]=delta.POLICY
            p=literal_proposal(t.bundle);p.update(expected_revision=1,revision_kind='interpretation_correction',update_mode='merge')
            p['nodes']=p['nodes'][:1];p['relations']=[];p['material_requests']=[]
            old_ids={n['id'] for n in t.bundle['research_loop']['current']['nodes']}
            expanded,report=delta.expand(t.bundle,p)
            self.assertEqual({n['id'] for n in expanded['nodes']},old_ids)
            p['update_mode']='replace'
            with self.assertRaisesRegex(ValueError,'Missing IDs'):delta.expand(t.bundle,p)

    def test_invalid_replacement_retains_old_valid_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);p=literal_proposal(t.bundle);t.execute('update_research_state',p,'first')
            before=copy.deepcopy(t.bundle['research_loop']['current']['nodes'][0])
            t.bundle['request'][delta.FIELD]=delta.POLICY
            p=literal_proposal(t.bundle);p.update(expected_revision=1,revision_kind='interpretation_correction',update_mode='merge')
            p['nodes']=p['nodes'][:1];p['nodes'][0]['claim']='Invented unsupported claim.'
            p['relations']=[];p['material_requests']=[]
            result=t.execute('update_research_state',p,'second')
            node=next(n for n in t.bundle['research_loop']['current']['nodes'] if n['id']==before['id'])
            self.assertEqual(node['claim'],before['claim'])
            self.assertIn(before['id'],result['acceptance']['retained_prior_nodes_after_rejected_replacement'])
            self.assertTrue(result['acceptance']['rejected'])

    def test_rejected_new_node_does_not_ack_material_or_spend_map_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['request'][delta.FIELD]=delta.POLICY
            p=literal_proposal(t.bundle);p['update_mode']='merge'
            t.execute('update_research_state',p,'first')
            current=copy.deepcopy(t.bundle['research_loop']['current'])
            ack=t.bundle['research_acquisition']['last_mapped_material']
            url='https://example.org/new';t.bundle['pages'][url]={'content':'Actual new observation: count is 16.'}
            t.bundle['research_acquisition']['pending_map_update']=True
            read=t.execute('inspect_research_state',{'url':url,'limit':1},'read')
            self.assertTrue(fusion.inspection_ready(t))
            p.update(expected_revision=1,material_sha256=read['material_sha256'],nodes=[copy.deepcopy(p['nodes'][0])],relations=[],material_requests=[])
            p['nodes'][0].update(id='new',claim='Fabricated count of 12.',evidence_ids=[read['evidence'][0]['evidence_id']])
            result=t.execute('update_research_state',p,'bad')
            self.assertFalse(result['committed']);self.assertEqual(t.bundle['research_loop']['current'],current)
            self.assertEqual(t.bundle['research_loop']['revision'],1)
            self.assertEqual(t.bundle['research_acquisition']['last_mapped_material'],ack)
            self.assertTrue(t.bundle['research_acquisition']['pending_map_update'])
            self.assertFalse(fusion.inspection_ready(t))
            self.assertEqual(fusion.advisory_forcing(t,None),'inspect_research_state')
            read=t.execute('inspect_research_state',{'url':url,'limit':1},'reread')
            self.assertTrue(fusion.inspection_ready(t))
            p['nodes'][0]['claim']=read['evidence'][0]['text']
            result=t.execute('update_research_state',p,'correct')
            self.assertTrue(result['committed']);self.assertEqual(t.bundle['research_loop']['revision'],2)

    def test_compact_reply_delivers_quarantine_reason_not_only_tool_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['request'][delivery.FIELD]=delivery.POLICY
            t.bundle['messages']=[{'role':'assistant','tool_calls':[{'id':'bad','type':'function',
                'function':{'name':'update_research_state','arguments':'{}'}}]},
                {'role':'tool','tool_call_id':'bad','content':json.dumps({'committed':False,
                    'acceptance':{'status':'rejected_no_change','rejected':[{'node_id':'bad','error':'Quote is not literal.'}]}})}]
            reply=json.loads(collection_context(t,forced_tool='inspect_research_state')[-1]['content'])
            self.assertFalse(reply['ok']);self.assertFalse(reply['committed'])
            self.assertEqual(reply['acceptance']['rejected'][0]['error'],'Quote is not literal.')

    def test_default_read_keeps_short_table_rows_and_delivers_headings_in_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['request'][reading_views.FIELD]=reading_views.POLICY
            url='https://example.org/report';t.bundle['pages']={url:{'content':'# Weekly report\n## Updated on 2 October 2026\n| Country | Count |\n| A | 16 |\n'}}
            result=t.execute('inspect_research_state',{'url':url,'limit':2},'read')
            self.assertEqual([s['text'].strip() for s in result['evidence']],['| Country | Count |','| A | 16 |'])
            self.assertIn('Updated on 2 October',str(result['source_context']))

    def test_merge_isolates_bad_requests_before_enforcing_final_map_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);t.bundle['request'][delta.FIELD]=delta.POLICY
            p=literal_proposal(t.bundle);p['update_mode']='merge'
            ident=p['nodes'][0]['id']
            p['material_requests']=[{'target':f'Request {i}','reason':'Read saved evidence.',
                'node_ids':[ident],'role':'gap','suggested_tool':'inspect_research_state'} for i in range(4)]
            t.execute('update_research_state',p,'initial')
            before=copy.deepcopy(t.bundle['research_loop'])
            p.update(expected_revision=1,revision_kind='interpretation_correction',nodes=[copy.deepcopy(p['nodes'][0])])
            p['nodes'][0].update(id='bad',claim='Invented unsupported observation.')
            p['material_requests']=[{'target':'Bad request','reason':'Read missing node.',
                'node_ids':['bad'],'role':'gap','suggested_tool':'inspect_research_state'}]
            result=t.execute('update_research_state',p,'rejected')
            self.assertFalse(result['committed']);self.assertEqual(t.bundle['research_loop'],before)
            p['nodes'][0]['claim']=before['current']['nodes'][0]['claim']
            with self.assertRaisesRegex(ValueError,'item limits'):t.execute('update_research_state',p,'over-cap')
            self.assertEqual(t.bundle['research_loop'],before)

    def test_native_envelope_isolates_long_bad_label_without_losing_good_observation(self):
        from ForecastAgent.research_loop import runtime
        from ForecastAgent.runtime.contracts import check_schema
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['request'][delta.FIELD]=delta.POLICY
            p=literal_proposal(t.bundle);p['update_mode']='merge'
            bad=copy.deepcopy(p['nodes'][0]);bad.update(id='bad',stage_basis='x'*181)
            p['nodes'].append(bad)
            tools=runtime.configure(t,[])
            presentation=next(x for x in tools if x['function']['name']=='update_research_state')
            with self.assertRaises(ValueError):check_schema(p,presentation['function']['parameters'])
            runtime.validate_args(t,'update_research_state',p,tools)
            result=t.execute('update_research_state',p,'native')
            self.assertTrue(result['committed']);self.assertIn(p['nodes'][0]['id'],result['acceptance']['accepted_node_ids'])
            self.assertIn('bad',result['acceptance']['accepted_node_ids'])
            retained=next(n for n in t.bundle['research_loop']['current']['nodes'] if n['id']=='bad')
            self.assertEqual(retained['claim'],bad['claim'])
            self.assertEqual(retained['event_stage'],'unknown');self.assertEqual(retained['stage_basis'],'')
            self.assertTrue(any(r.get('field')=='stage_basis' for r in result['acceptance']['rejected']))

    def test_duplicate_old_binding_cannot_ack_unreviewed_body_and_new_read_still_becomes_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['request'][delta.FIELD]=delta.POLICY
            p=literal_proposal(t.bundle);p['update_mode']='merge';t.execute('update_research_state',p,'first')
            url='https://example.org/new';t.bundle['pages'][url]={'content':'A genuinely new baseline measurement is now available.'}
            t.bundle['research_acquisition']['pending_map_update']=True
            p.update(expected_revision=1,material_sha256=state.catalog(t.bundle)['material_sha256'],
                nodes=[copy.deepcopy(p['nodes'][0])],relations=[],material_requests=[])
            p['nodes'][0]['id']='duplicate'
            result=t.execute('update_research_state',p,'duplicate')
            self.assertTrue(result['committed']);self.assertFalse(result['material_acknowledged'])
            self.assertTrue(t.bundle['research_acquisition']['pending_map_update'])
            t.execute('inspect_research_state',{'url':url,'limit':1},'read')
            self.assertTrue(fusion.inspection_ready(t))

    def test_same_saved_body_reading_update_is_audited_without_false_acquisition_ack(self):
        from ForecastAgent.research_loop import runtime
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['request'][delta.FIELD]=delta.POLICY
            p=literal_proposal(t.bundle);p['update_mode']='merge';t.execute('update_research_state',p,'first')
            old=t.bundle['research_loop']['current']['nodes'][0]
            t.bundle['research_acquisition']['pending_map_update']=True
            p.update(expected_revision=1,nodes=[copy.deepcopy(p['nodes'][0])],relations=[],material_requests=[])
            p['nodes'][0]['id']='another_note'
            result=t.execute('update_research_state',p,'reading')
            self.assertTrue(result['committed']);self.assertFalse(result['material_acknowledged'])
            normal=result['acceptance']['delta']['revision_kind_normalization']
            self.assertEqual(normal['requested'],'material_update')
            self.assertEqual(t.bundle['research_loop']['events'][-1]['revision_kind'],'interpretation_correction')
            self.assertTrue(state.audit(t.bundle)['unreviewed_material_change'])
            schema=next(x for x in runtime.filter_tools(t,runtime.configure(t,[])) if x['function']['name']=='update_research_state')
            self.assertEqual(schema['function']['parameters']['properties']['revision_kind']['enum'],['interpretation_correction'])

    def test_compact_context_preserves_exact_read_and_actual_tool_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['request'][delivery.FIELD]=delivery.POLICY
            t.bundle['request'][delta.FIELD]=delta.POLICY
            t.bundle['messages']=[{'role':'system','content':'old duplicate guide '*2000}]
            t.bundle['research_acquisition']['pending_map_update']=True
            t.execute('inspect_research_state',{'limit':1},'inspect')
            refs=fusion.binding_frame(t)['inspected_evidence'];ctx=collection_context(t,forced_tool='update_research_state')
            self.assertLess(len(encode(ctx)),28000)
            delivered=json.loads(ctx[1]['content'])
            self.assertEqual(delivered['research_binding_frame']['inspected_evidence'],refs)
            self.assertEqual(delivered['available_tools'],['update_research_state'])
            self.assertEqual(delivered['next_action']['tool'],'update_research_state')
            self.assertIn(t.bundle['request']['resolution_criteria'],ctx[1]['content'])

    def test_real_local_dispatch_replay_binds_new_source_without_ledger_reset(self):
        from unittest.mock import patch
        from ForecastAgent.analysis.pilot import save
        from ForecastAgent.research_loop.snapshot_loop_trial import entry,QUOTAS
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);t=enable(task(tmp/'seed'));p=literal_proposal(t.bundle)
            t.execute('update_research_state',p,'first')
            url='https://example.org/new';t.bundle['pages'][url]={'content':'New report explicitly states the measured country count is 16. '*3}
            t.bundle['research_acquisition']['pending_map_update']=True;t.save()
            original=copy.deepcopy(t.bundle)
            def fake_model(messages,key,**kwargs):
                if kwargs['forced_tool']=='inspect_research_state':args={'url':url,'limit':1}
                else:
                    data=json.loads(messages[1]['content']);frame=data['research_binding_frame'];ref=frame['inspected_evidence'][0]
                    node=copy.deepcopy(p['nodes'][0]);node.update(id='newCount',claim='New report explicitly states the measured country count is 16.',evidence_ids=[ref['evidence_id']],stage_basis='')
                    args={**p,'expected_revision':frame['revision'],'material_sha256':frame['material_sha256'],
                        'nodes':[node],'relations':[],'material_requests':[],'retired_node_ids':[],'update_mode':'merge'}
                return {'role':'assistant','tool_calls':[{'id':kwargs['forced_tool'],'type':'function','function':
                    {'name':kwargs['forced_tool'],'arguments':json.dumps(args)}}]}
            with patch('ForecastAgent.research_loop.snapshot_loop_trial.ask_model',fake_model),patch.dict('os.environ',{'OPENROUTER_API_KEY':'fake'}):
                result=entry(t.path,tmp/'replay',True)
            self.assertEqual(result['status'],'new_saved_evidence_bound_in_later_map')
            final=json.loads((tmp/'replay/collection/bundle.json').read_text())
            self.assertEqual(final['pages'],original['pages'])
            for key in QUOTAS:self.assertEqual(final.get(key),original.get(key))
            self.assertEqual(json.loads(t.path.read_text()),original)
