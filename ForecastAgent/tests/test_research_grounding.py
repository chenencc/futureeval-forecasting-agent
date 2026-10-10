"""Coverage navigation must preserve raw text, scope, observations and budgets."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from ForecastAgent.research_loop import grounding, state, fusion, runtime, simple_map
from ForecastAgent.research_loop.acceptance import accept
from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.runtime.context import collection_context, encode, project_assistant
from ForecastAgent.tools.registry import TOOLS
from ForecastAgent.tests.test_research_fusion import task
from ForecastAgent.tests.test_research_simple_map import literal_proposal
from ForecastAgent.analysis.pilot import digest


def enable(t):
    t.bundle['request'][grounding.FIELD]=grounding.POLICY
    return t


class GroundingTests(unittest.TestCase):
    def test_whole_saved_range_is_visible_before_row_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));text='Market Open.\n\n| Date | Close |\n| --- | --- |\n'
            text+=''.join(f'| Oct {d}, 2026 | {100+d} |\n' for d in range(9,4,-1))
            t.bundle['pages']={'https://example.org/history':{'content':text}}
            before=copy.deepcopy(t.bundle['pages']);budget=t.budget()
            result=t.execute('inspect_research_state',{'start_date':'2026-10-05','end_date':'2026-10-08','limit':2},'unused')
            self.assertEqual(result['total_matches'],4)
            self.assertEqual(result['reading_scope']['unread_matching_spans'],2)
            source=result['source_catalog']['sources'][0]
            self.assertEqual((source['lexical_date_start'],source['lexical_date_end']),('2026-10-05','2026-10-09'))
            self.assertEqual(source['lexical_date_count'],5)
            self.assertIn('Market Open',result['source_context'][0]['text'])
            self.assertTrue(any('Date | Close' in s['text'] for s in result['source_context']))
            for span in result['evidence']+result['source_context']:
                self.assertEqual(span['text'],text[span['start']:span['end']])
                self.assertEqual(span['body_sha256'],hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(t.bundle['pages'],before);self.assertEqual(t.budget(),budget)

    def test_restricted_original_coverage_never_exposes_hidden_dates_or_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));url='https://example.org/report'
            text='Hidden status and 2028-09-01.\n\nVisible report dated 2026-10-07 contains measured background information.\n'
            a=text.index('Visible');h=hashlib.sha256(text.encode()).hexdigest()
            t.bundle['pages']={url:{'content':text}}
            t.bundle['research_map_visible_references']=[{'url':url,'body_sha256':h,'start':a,'end':len(text)}]
            result=t.execute('inspect_research_state',{},'unused')
            entry=result['source_catalog']['sources'][0]
            self.assertEqual(entry['lexical_date_end'],'2026-10-07')
            self.assertEqual(entry['accessible_chars'],len(text)-a)
            self.assertNotIn('Hidden',str(result))

    def test_excluded_and_truncated_sources_cannot_imply_full_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));t.bundle['pages']['https://example.org/blocked']={'content':'Just a moment. Verify you are human.'}
            next(iter(t.bundle['pages'].values()))['content_truncated']=True
            result=t.execute('inspect_research_state',{},'unused')
            self.assertEqual(result['source_catalog']['total_sources'],1)
            self.assertTrue(result['source_catalog']['sources'][0]['source_truncated'])
            self.assertEqual(len(result['excluded_sources']),1)

    def test_date_navigation_handles_different_text_formats_without_claiming_event_time(self):
        self.assertEqual(grounding.dates('2026-10-07; October 8, 2026; 9 October 2026; Feb 31, 2026'),
            [('2026-10-07','2026-10-07'),('2026-10-08','October 8, 2026'),('2026-10-09','9 October 2026')])
        for args in ({'start_date':'2026-10-07'},{'start_date':'2026-10-09','end_date':'2026-10-07'},{'url':'https://missing.org'}):
            with self.assertRaises(ValueError):grounding.select({'sources':{},'spans':{}},args)

    def test_unsupported_stage_quarantines_label_and_preserves_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));p=literal_proposal(t.bundle);original=copy.deepcopy(p)
            p['nodes']=p['nodes'][:1];p['nodes'][0]['stage_basis']='Invented completed status'
            p['material_requests']=[];raw=copy.deepcopy(p);pages=copy.deepcopy(t.bundle['pages'])
            result=t.execute('update_research_state',p,'unused')
            node=t.bundle['research_loop']['current']['nodes'][0]
            self.assertEqual(node['claim'],original['nodes'][0]['claim'])
            self.assertEqual(node['event_stage'],'unknown')
            self.assertFalse(node['stage_basis_binding_verified'])
            self.assertEqual(result['acceptance']['rejected'][0]['section'],'node_labels')
            self.assertIn(node['id'],result['acceptance']['accepted_node_ids'])
            self.assertEqual(p,raw);self.assertEqual(t.bundle['pages'],pages)

    def test_literal_stage_support_does_not_certify_its_semantic_meaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));p=literal_proposal(t.bundle)
            p['nodes']=p['nodes'][:1];p['material_requests']=[]
            p['nodes'][0]['stage_basis']='Revenue was 24 billion dollars.'
            t.execute('update_research_state',p,'unused')
            node=t.bundle['research_loop']['current']['nodes'][0]
            self.assertTrue(node['stage_basis_binding_verified'])
            self.assertFalse(node['interpretation_verified'])

    def test_unknown_stage_needs_no_extra_observation_or_model_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));p=literal_proposal(t.bundle);p['nodes']=p['nodes'][:1];p['material_requests']=[]
            p['nodes'][0].update(event_stage='unknown',stage_basis='')
            t.execute('update_research_state',p,'unused')
            self.assertEqual(len(t.bundle['research_loop']['current']['nodes']),1)

    def test_old_policy_schema_and_proposals_remain_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);p=literal_proposal(t.bundle)
            check_schema(p,simple_map.MAP_SCHEMA)
            self.assertNotIn('stage_basis',simple_map.MAP_SCHEMA['properties']['nodes']['items']['properties'])
            result=t.execute('update_research_state',p,'unused')
            self.assertNotIn('stage_grounding',result['acceptance'])
            self.assertEqual(t.bundle['research_loop']['current']['nodes'][0]['event_stage'],'completed')
            before=copy.deepcopy(t.bundle['pages'])
            self.assertNotIn('source_catalog',t.execute('inspect_research_state',{},'unused'))
            self.assertEqual(t.bundle['pages'],before)

    def test_numeric_date_row_cannot_supply_stage_but_status_text_can(self):
        quote='| Oct 9, 2026 | 109.4 | - |'
        original={'id':'n','kind':'observation','event_stage':'completed','stage_basis':quote,'evidence_ids':['r']}
        material={'spans':{'r':{'text':quote+' Market Open'}}}
        chosen, errors=grounding.isolate_stage(original,material)
        self.assertEqual(chosen['event_stage'],'unknown');self.assertEqual(len(errors),1)
        original['stage_basis']='Market Open'
        chosen,errors=grounding.isolate_stage(original,material)
        # A status quote establishes provenance; a wrong completed interpretation
        # remains a model error and is never marked semantically verified.
        self.assertEqual(chosen['event_stage'],'completed');self.assertEqual(errors,[])

    def test_legacy_optional_field_does_not_activate_new_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp);p=literal_proposal(t.bundle);p['nodes'][0]['stage_basis']='Old unverified explanation'
            p['nodes']=p['nodes'][:1];p['material_requests']=[]
            result=accept(t.bundle,p,map_protocol=simple_map.PROTOCOL)
            self.assertEqual(result['acceptance']['accepted_node_ids'],[p['nodes'][0]['id']])

    def test_opt_in_tool_schema_requires_support_and_rejects_unknown_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));tools=runtime.configure(t,TOOLS)
            node=next(e for e in tools if e['function']['name']=='update_research_state')['function']['parameters']['properties']['nodes']['items']
            self.assertIn('stage_basis',node['required'])
            read=next(e for e in tools if e['function']['name']=='inspect_research_state')['function']['parameters']['properties']
            self.assertIn('start_date',read)
            t.bundle['request'][grounding.FIELD]='invented'
            with self.assertRaisesRegex(ValueError,'Unknown saved-coverage'):fusion.initialize(t)

    def test_source_context_survives_dispatch_without_budget_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));text='Live status and title.\n\n| Date | Close |\n| --- | --- |\n| Oct 9, 2026 | 109 |\n'
            t.bundle['pages']={'https://example.org/history':{'content':text}}
            t.execute('inspect_research_state',{'query':'Oct 9','limit':1},'unused')
            t.bundle['research_acquisition']['pending_map_update']=True
            t.bundle['messages']=[{'role':'system','content':'Frozen research instructions.'}]
            before=t.budget();frame=fusion.binding_frame(t)
            self.assertIn('Live status',frame['source_context'][0]['text'])
            self.assertLessEqual(len(encode(collection_context(t))),28000)
            self.assertEqual(t.budget(),before)
            t.bundle['pages']['https://example.org/history']['content']+=' new version'
            self.assertEqual(fusion.binding_frame(t)['source_context'],[])

    def test_section_date_and_title_are_delivered_with_exact_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));url='https://example.org/changelog'
            text=('Document opening.\n\n## October 1, 2026\n\n## Product release\n\n'
                  'The named product is rolling out in the prior period.\n\n'
                  '## September 1, 2026\n\n## Older product\n\nOlder announcement.\n')
            t.bundle['pages']={url:{'content':text}}
            result=t.execute('inspect_research_state',{'query':'rolling out','limit':1},'unused')
            contexts=result['source_context']
            self.assertTrue(any('October 1, 2026' in s['text'] for s in contexts))
            self.assertTrue(any('Product release' in s['text'] for s in contexts))
            self.assertFalse(any('September 1' in s['text'] for s in contexts))
            for s in contexts:
                self.assertEqual(s['text'],text[s['start']:s['end']])

    def test_unseen_heading_gap_never_becomes_context_for_another_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));url='https://example.org/report'
            text='## October 1, 2026\n\nHidden narrative establishes a different period.\n\nVisible target row lacks a date.\n'
            a=text.index('Visible');h=hashlib.sha256(text.encode()).hexdigest()
            t.bundle['pages']={url:{'content':text}}
            t.bundle['research_map_visible_references']=[
                {'url':url,'body_sha256':h,'start':0,'end':text.index('Hidden')},
                {'url':url,'body_sha256':h,'start':a,'end':len(text)}]
            result=t.execute('inspect_research_state',{'query':'Visible','limit':1},'unused')
            self.assertFalse(any('October 1' in s['text'] for s in result['source_context']))

    def test_time_annotation_is_isolated_while_literal_observation_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));p=literal_proposal(t.bundle)
            p['nodes']=p['nodes'][:1];p['material_requests']=[]
            p['nodes'][0].update(event_stage='unknown',stage_basis='',event_time='Unbound date label',time_status='source_stated')
            raw=copy.deepcopy(p);budget=t.budget()
            result=t.execute('update_research_state',p,'unused')
            n=t.bundle['research_loop']['current']['nodes'][0]
            self.assertEqual(n['claim'],p['nodes'][0]['claim'])
            self.assertEqual((n['event_time'],n['time_status']),('','unknown'))
            error=next(e for e in result['acceptance']['rejected'] if e['field']=='event_time')
            self.assertEqual(error['proposed'],'Unbound date label')
            self.assertFalse(n['interpretation_verified'])
            self.assertEqual(p,raw);self.assertEqual(t.budget(),budget)

    def test_bad_claim_still_rejected_after_label_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));p=literal_proposal(t.bundle)
            p['nodes']=p['nodes'][:1];p['material_requests']=[]
            p['nodes'][0].update(claim='Invented revenue was 999 dollars.',stage_basis='',
                event_time='Unbound date label',time_status='source_stated')
            from ForecastAgent.research_loop.acceptance import MapAcceptanceError
            with self.assertRaises(MapAcceptanceError):t.execute('update_research_state',p,'unused')
            self.assertEqual(t.bundle['research_loop']['revision'],0)

    def test_label_policy_version_prevents_reusing_older_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));p=literal_proposal(t.bundle)
            p['nodes']=p['nodes'][:1];p['material_requests']=[]
            p['nodes'][0].update(event_stage='unknown',stage_basis='')
            first=t.execute('update_research_state',p,'unused')
            self.assertEqual(first['acceptance']['label_grounding_policy'],grounding.LABEL_POLICY)
            self.assertTrue(t.execute('update_research_state',p,'unused')['cached'])
            event=t.bundle['research_loop']['events'][-1]
            event['acceptance'].pop('label_grounding_policy')
            event['event_sha256']=digest({k:v for k,v in event.items() if k!='event_sha256'})
            with self.assertRaisesRegex(ValueError,'Stale research revision'):
                t.execute('update_research_state',p,'unused')
            self.assertEqual(t.bundle['research_loop']['revision'],1)

    def test_plain_reasoning_is_omitted_but_opaque_continuity_is_preserved(self):
        original={'role':'assistant','content':None,'tool_calls':[{'id':'c','function':
            {'name':'update_research_state','arguments':'{"expected_revision":0}'}}],
            'reasoning':'r'*5000,'reasoning_details':[
                {'type':'reasoning.text','text':'r'*5000,'format':'unknown'},
                {'type':'reasoning.encrypted','data':'opaque'},
                {'type':'reasoning.text','text':'signed','signature':'opaque-signature'}]}
        before=copy.deepcopy(original);out=project_assistant(original)
        self.assertNotIn('reasoning',out)
        self.assertEqual(out['reasoning_details'],before['reasoning_details'][1:])
        self.assertEqual(out['tool_calls'],before['tool_calls']);self.assertEqual(original,before)

    def test_large_duplicate_reasoning_cannot_block_a_failed_tool_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));p=literal_proposal(t.bundle)
            t.execute('inspect_research_state',{},'unused')
            t.bundle['messages']=[{'role':'system','content':'Frozen instructions.'},
                {'role':'assistant','content':None,'reasoning':'r'*8500,
                 'reasoning_details':[{'type':'reasoning.text','text':'r'*8500}],
                 'tool_calls':[{'id':'failed-map','type':'function','function':{
                    'name':'update_research_state','arguments':encode(p)}}]},
                {'role':'tool','tool_call_id':'failed-map','content':encode({'error':'Wrong literal annotation'})}]
            before=copy.deepcopy(t.bundle['messages']);budget=t.budget()
            result=collection_context(t)
            self.assertLessEqual(len(encode(result)),28000)
            assistant=next(m for m in result if m['role']=='assistant')
            self.assertEqual(assistant['tool_calls'],before[1]['tool_calls'])
            self.assertNotIn('reasoning',assistant);self.assertNotIn('reasoning_details',assistant)
            self.assertEqual(t.bundle['messages'],before);self.assertEqual(t.budget(),budget)

    def test_new_schema_lists_exact_inspected_and_context_handles(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=enable(task(tmp));reading=t.execute('inspect_research_state',{},'unused')
            tools=runtime.filter_tools(t,runtime.configure(t,[]))
            p=next(e for e in tools if e['function']['name']=='update_research_state')['function']['parameters']
            refs=p['properties']['nodes']['items']['properties']['evidence_ids']['items']['enum']
            self.assertTrue({s['evidence_id'] for s in reading['evidence']+reading['source_context']}<=set(refs))
            unknown=literal_proposal(t.bundle)['nodes'][-1]
            unknown.update(kind='unknown',claim_origin='gap',gap_reason='unreviewed',evidence_ids=[],stage_basis='')
            check_schema(unknown,p['properties']['nodes']['items'])


if __name__=='__main__':unittest.main()
