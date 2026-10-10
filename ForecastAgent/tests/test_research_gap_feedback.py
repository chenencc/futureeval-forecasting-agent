"""Native gap-to-action receipts, partial review isolation and durable limits."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import gap_feedback as gf, delta, fusion, runtime, state, grounding, reading_views, delivery
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_simple_map import literal_proposal


def task(root):
    original=source_bundle()
    request=copy.deepcopy(original['request'])
    request.update(research_acquisition_policy=fusion.POLICY_NAME,
        research_update_policy=delta.POLICY, research_gap_policy=gf.POLICY,
        research_grounding_policy=grounding.POLICY,
        research_reading_policy=reading_views.POLICY, research_delivery_policy=delivery.POLICY)
    t=RetrievalTask(Path(root),request)
    t.bundle['pages']=copy.deepcopy(original['pages'])
    t.bundle['plan']=[{'id':'release','priority':'critical','condition':'Target-period revenue.',
        'expected_source':'Official release','query':'target revenue'}]
    fusion.initialize(t)
    t.bundle['research_acquisition']['pending_map_update']=True
    t.save()
    return t


def receipt(t, url, disposition='incorporated', node_ids=None, effect='narrows'):
    inventory=gf.materials(t)
    m=next(m for m in inventory.values() if m['url']==url)
    ref=next(r['evidence_id'] for r in fusion.inspected_references(t,state.catalog(t.bundle)) if r['url']==url)
    return {'material_id':m['material_id'],'disposition':disposition,'evidence_ids':[ref],
        'node_ids':node_ids if node_ids is not None else ['observed_report'],
        'gap_ids':[], 'related_material_ids':[], 'effect':effect,
        'reason':'Retain the dated revenue baseline; the future target remains uncertain.'}


def initial(t):
    t.execute('inspect_research_state',{'url':'https://example.org/report','limit':3},'test')
    p=literal_proposal(t.bundle)
    p.update(update_mode='merge',material_reviews=[receipt(t,'https://example.org/report')])
    p['nodes'][1]['evidence_ids']=[]
    p['material_requests'][0].update(decision_impact='A target-period release could change the measured value.',
        importance='high',availability='future_event')
    return t.execute('update_research_state',p,'test')


def patch(t, reviews):
    return {'expected_revision':t.bundle['research_loop']['revision'],
        'material_sha256':state.catalog(t.bundle)['material_sha256'],
        'revision_kind':'material_update','update_mode':'merge','nodes':[], 'relations':[],
        'material_requests':[],'retired_node_ids':[], 'supporting_path':'','alternative_path':'',
        'revision_reason':'Reviewed the new source; explain its limited relevance without changing prior facts.',
        'material_reviews':reviews}


class GapFeedbackTests(unittest.TestCase):
    def test_valid_source_review_explains_actual_delta_and_preserves_quotas(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root); before=t.budget(); pages=copy.deepcopy(t.bundle['pages'])
            result=initial(t)
            self.assertTrue(result['material_acknowledged'])
            self.assertFalse(t.bundle['research_acquisition']['pending_map_update'])
            event=t.bundle['research_gap_feedback']['events'][-1]
            self.assertIn('observed_report',event['actual_graph_delta']['added_node_ids'])
            self.assertFalse(event['explanation_verified'])
            self.assertEqual(t.budget(),before);self.assertEqual(t.bundle['pages'],pages)
            self.assertEqual(len(t.bundle.get('model_attempts',[])),0)
            state.initialize(t.bundle);gf.initialize(t)

    def test_gap_drives_action_with_intent_snapshot_and_native_budget(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t);g=gf.gaps(t)[0];seen=[]
            def native(name,args,key):
                seen.append(args);t.bundle['searches'].append({'status':'received','results':[]})
                return {'ok':True}
            before=t.budget()['tavily_basic_remaining']
            fusion.execute(t,'search_tavily',{'query':'official guidance',gf.LINK:[g['gap_id']]},'test',native)
            e=t.bundle['research_acquisition']['events'][-1]
            self.assertEqual(e[gf.LINK],[g['gap_id']])
            self.assertEqual(e['gap_intents'][0]['decision_impact'],g['decision_impact'])
            self.assertNotIn(gf.LINK,seen[0])
            self.assertEqual(t.budget()['tavily_basic_remaining'],before-1)
            with self.assertRaisesRegex(ValueError,'already reserved'):
                fusion.execute(t,'search_tavily',{'query':'official guidance'},'test',native)
            self.assertEqual(len(seen),1)

    def test_unknown_gap_fails_before_network_or_quota(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);before=copy.deepcopy(t.bundle)
            with self.assertRaisesRegex(ValueError,'listed gap IDs'):
                fusion.execute(t,'search_exa',{'query':'q',gf.LINK:['invented']},'test',lambda *a:self.fail('Network forbidden'))
            self.assertEqual(t.bundle,before)

    def test_unbound_receipt_cannot_acknowledge_new_material(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            url='https://example.org/new';t.bundle['pages'][url]={'content':'Revenue guidance concerns a different period. '*20}
            t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            r=receipt(t,url)
            result=t.execute('update_research_state',patch(t,[r]),'test')
            self.assertFalse(result['material_acknowledged'])
            self.assertIn('retained observation',result['gap_feedback']['rejected_reviews'][0]['error'])
            self.assertEqual(result['acceptance']['status'],'partial')
            recorded=t.bundle['research_gap_feedback']['events'][-1]
            self.assertEqual(recorded['rejected_reviews'],result['gap_feedback']['rejected_reviews'])
            count=len(t.bundle['research_gap_feedback']['events'])
            t.execute('update_research_state',patch(t,[r]),'test')
            self.assertEqual(len(t.bundle['research_gap_feedback']['events']),count)
            self.assertEqual(gf.view(t)['pending_material_count'],1)
            self.assertTrue(t.bundle['research_acquisition']['pending_map_update'])

    def test_irrelevant_source_can_be_reviewed_without_a_graph_change(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t);revision=t.bundle['research_loop']['revision']
            url='https://example.org/unrelated';t.bundle['pages'][url]={'content':'This is an unrelated company and historical quarter. '*20}
            t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            p=patch(t,[receipt(t,url,'irrelevant',[], 'no_change')]);before=t.budget()
            result=t.execute('update_research_state',p,'test')
            self.assertFalse(result['committed']);self.assertTrue(result['material_acknowledged'])
            self.assertEqual(t.bundle['research_loop']['revision'],revision)
            self.assertEqual(result['gap_feedback']['actual_graph_delta']['added_node_ids'],[])
            self.assertEqual(gf.view(t)['pending_material_count'],0)
            self.assertEqual(t.budget(),before)
            count=len(t.bundle['research_gap_feedback']['events'])
            t.execute('update_research_state',p,'test')
            self.assertEqual(len(t.bundle['research_gap_feedback']['events']),count)

    def test_deferred_review_keeps_work_pending_and_does_not_suggest_no(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            url='https://example.org/future';t.bundle['pages'][url]={'content':'The next release is planned for a future date. '*20}
            t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            result=t.execute('update_research_state',patch(t,[receipt(t,url,'deferred',[],'unknown')]),'test')
            self.assertFalse(result['material_acknowledged'])
            self.assertEqual(gf.view(t)['pending_material_count'],1)
            self.assertFalse(gf.view(t)['meaning_verified'])

    def test_unread_source_cannot_be_declared_irrelevant(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            url='https://example.org/unread';t.bundle['pages'][url]={'content':'A new source has not been delivered to the agent. '*20}
            m=next(m for m in gf.materials(t).values() if m['url']==url)
            r={'material_id':m['material_id'],'disposition':'irrelevant','evidence_ids':[m['reference_ids'][0]],
                'node_ids':[],'gap_ids':[],'related_material_ids':[],'effect':'no_change','reason':'Allegedly unrelated.'}
            result=t.execute('update_research_state',patch(t,[r]),'test')
            self.assertIn('inspected spans',result['gap_feedback']['rejected_reviews'][0]['error'])
            self.assertFalse(result['material_acknowledged'])

    def test_conflicting_receipt_keeps_both_bound_observations(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            url='https://example.org/revision'
            t.bundle['pages'][url]={'content':'Revenue was 26 billion dollars. This report revises the baseline. '*20}
            t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            r=receipt(t,url,'conflict',['observed_report','revised_report'])
            n=delta.node_input(t.bundle['research_loop']['current']['nodes'][0])
            n.update(id='revised_report',claim='Revenue was 26 billion dollars.',evidence_ids=r['evidence_ids'])
            p=patch(t,[r]);p['nodes']=[n]
            result=t.execute('update_research_state',p,'test')
            self.assertEqual(len(result['gap_feedback']['accepted_reviews']),1)
            self.assertEqual(result['gap_feedback']['rejected_reviews'],[])
            self.assertTrue({'observed_report','revised_report'} <= {n['id'] for n in t.bundle['research_loop']['current']['nodes']})
            self.assertFalse(result['gap_feedback']['accepted_reviews'][0]['meaning_verified'])

    def test_duplicate_receipt_names_existing_material_and_does_not_add_node(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t);old=next(iter(gf.materials(t)))
            url='https://example.org/reprint'
            t.bundle['pages'][url]=copy.deepcopy(t.bundle['pages']['https://example.org/report'])
            t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            r=receipt(t,url,'duplicate',[],'no_change');r['related_material_ids']=[old]
            revision=t.bundle['research_loop']['revision']
            result=t.execute('update_research_state',patch(t,[r]),'test')
            self.assertTrue(result['material_acknowledged'])
            self.assertEqual(result['gap_feedback']['actual_graph_delta']['added_node_ids'],[])
            self.assertEqual(t.bundle['research_loop']['revision'],revision)

    def test_unrelated_reference_cannot_support_a_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            url='https://example.org/another';t.bundle['pages'][url]={'content':'Another company discusses a different product. '*20}
            t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            r=receipt(t,url,'irrelevant',[],'no_change')
            r['evidence_ids']=t.bundle['research_loop']['current']['nodes'][0]['evidence_ids']
            result=t.execute('update_research_state',patch(t,[r]),'test')
            self.assertEqual(len(result['gap_feedback']['accepted_reviews']),0)
            self.assertFalse(result['material_acknowledged'])

    def test_inspected_handles_survive_multiple_reads_without_gaining_source_credit(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);t.execute('inspect_research_state',{'url':'https://example.org/report','limit':1},'test')
            first=copy.deepcopy(t.bundle['research_acquisition']['inspected_references'])
            url='https://example.org/another';t.bundle['pages'][url]={'content':'A second saved original contains a different fact. '*20}
            before=t.budget();t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            self.assertTrue({r['evidence_id'] for r in first} <= {r['evidence_id'] for r in t.bundle['research_acquisition']['inspected_references']})
            self.assertEqual(t.budget(),before)

    def test_bad_review_is_isolated_from_valid_sibling_and_bound_node(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root)
            t.execute('inspect_research_state',{'url':'https://example.org/report','limit':3},'test')
            p=literal_proposal(t.bundle);p['nodes'][1]['evidence_ids']=[]
            p.update(update_mode='merge',material_reviews=[receipt(t,'https://example.org/report'),{'material_id':'bad'}])
            result=t.execute('update_research_state',p,'test')
            self.assertIn('observed_report',result['acceptance']['accepted_node_ids'])
            self.assertEqual(len(result['gap_feedback']['accepted_reviews']),1)
            self.assertEqual(len(result['gap_feedback']['rejected_reviews']),1)

    def test_material_body_or_visible_scope_changes_invalidate_old_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t);old=set(gf.materials(t))
            t.bundle['pages']['https://example.org/report']['content'] += ' A later update.'
            t.bundle['pages']['https://example.org/report'].pop('content_sha256',None)
            self.assertTrue(set(gf.materials(t)).isdisjoint(old))
            self.assertEqual(gf.view(t)['pending_material_count'],1)

    def test_resume_keeps_receipts_and_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t);t.save();budget=t.budget()
            resumed=RetrievalTask(Path(root),t.bundle['request']);gf.initialize(resumed)
            self.assertEqual(gf.view(resumed)['pending_material_count'],0)
            self.assertEqual(resumed.budget(),budget)
            resumed.bundle['research_gap_feedback']['events'][0]['model_explanation']='tampered'
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):gf.initialize(resumed)

    def test_native_context_exposes_pending_ids_and_priorities_without_network(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            messages=delivery.context(t,forced='inspect_research_state')
            self.assertLess(len(json.dumps(messages)),28000)
            payload=json.loads(messages[1]['content'])
            g=payload['research_binding_frame']['gap_feedback']['gaps'][0]
            self.assertEqual(g['importance'],'high');self.assertEqual(g['availability'],'future_event')
            self.assertFalse(g['meaning_verified'])
            tools=runtime.filter_tools(t,runtime.configure(t,[]))
            update=next(e for e in tools if e['function']['name']=='update_research_state')
            self.assertIn('material_reviews',update['function']['parameters']['required'])

    def test_span_pagination_cursor_survives_context_and_read_then_update_is_forced(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root)
            reply=t.execute('inspect_research_state',{'url':'https://example.org/report','limit':1},'test')
            cursor=reply['reading_cursor']
            self.assertEqual(cursor['span_offset'],0)
            self.assertEqual(cursor['next_span_offset'],1)
            frame=fusion.binding_frame(t)
            self.assertEqual(frame['last_reading_cursor'],cursor)
            messages=delivery.context(t,forced='update_research_state')
            self.assertEqual(json.loads(messages[1]['content'])['research_binding_frame']['last_reading_cursor'],cursor)
            self.assertEqual(fusion.advisory_forcing(t,None),'update_research_state')
            exhausted=t.execute('inspect_research_state',{'url':'https://example.org/report','offset':1000,'limit':1},'test')
            self.assertEqual(exhausted['reading_cursor']['delivered_spans'],0)
            self.assertIsNone(exhausted['reading_cursor']['next_span_offset'])
            self.assertEqual(exhausted['reading_cursor']['total_matching_spans'],cursor['total_matching_spans'])

    def test_blank_update_explanation_cannot_commit(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);t.execute('inspect_research_state',{'limit':3},'test')
            p=literal_proposal(t.bundle);p.update(update_mode='merge',material_reviews=[],revision_reason='')
            with self.assertRaisesRegex(ValueError,'Explain what changed'):
                t.execute('update_research_state',p,'test')
            self.assertEqual(t.bundle['research_loop']['revision'],0)

    def test_four_inspected_spans_fit_receipt_and_keep_exact_source_validation(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root)
            t.bundle['pages']['https://example.org/report']['content'] *= 5
            read=t.execute('inspect_research_state',{'url':'https://example.org/report','limit':4},'test')
            self.assertEqual(len(read['evidence']),4)
            p=literal_proposal(t.bundle);p['nodes'][1]['evidence_ids']=[]
            r=receipt(t,'https://example.org/report')
            r['evidence_ids']=[s['evidence_id'] for s in read['evidence']]
            p.update(update_mode='merge',material_reviews=[r])
            result=t.execute('update_research_state',p,'test')
            self.assertEqual(result['gap_feedback']['rejected_reviews'],[])
            self.assertTrue(result['material_acknowledged'])

    def test_program_explanation_does_not_claim_rejected_node_was_added(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            p=patch(t,[])
            n=delta.node_input(t.bundle['research_loop']['current']['nodes'][0])
            n.update(id='invented',claim='An invented nonliteral fact.')
            p['nodes']=[n];p['revision_reason']='Added invented observation.'
            result=t.execute('update_research_state',p,'test')
            report=result['gap_feedback']['program_explanation']
            self.assertEqual(report['node_changes']['added_node_ids'],[])
            self.assertIn('invented',report['rejected_proposed_node_ids'])
            self.assertEqual(result['gap_feedback']['model_explanation'],p['revision_reason'])
            self.assertFalse(result['gap_feedback']['explanation_verified'])

    def test_saved_trial_prepare_exposes_feedback_without_model_request(self):
        from ForecastAgent.research_loop.snapshot_loop_trial import entry
        with tempfile.TemporaryDirectory() as root:
            t=task(Path(root)/'original');initial(t);t.save()
            with mock_patch('ForecastAgent.research_loop.snapshot_loop_trial.ask_model',
                            side_effect=AssertionError('No HTTP in prepare mode')):
                result=entry(Path(root)/'original/bundle.json',Path(root)/'prepared',False,gap_policy=gf.POLICY)
            self.assertEqual(result['status'],'prepared')
            self.assertEqual(result['usage']['totals']['http_attempts'],0)
            self.assertEqual(result['gap_feedback']['policy'],gf.POLICY)
            self.assertTrue(result['original_pages_preserved'])


if __name__ == '__main__':
    unittest.main()
