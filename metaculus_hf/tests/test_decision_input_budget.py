import copy,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from metaculus_agent import decision_tokens as tokens,decision_model as model,decision_input as inputs,evidence_packing as packing,decision_analysis as analysis
from metaculus_agent.analysis_policy import decision_route,DECISION_MODEL
from ForecastAgent.competition import mercury as primitives
from ForecastAgent.analysis import mercury_evidence_chain as spans
from test_evidence_packing import source

class InputBudgetTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'METACULUS_DECISION_INPUT_PROFILE':'role-aware-v2'})
        env.start();self.addCleanup(env.stop)
    def test_stable_profile_preserves_trace_fields_and_old_rubric(self):
        with patch.dict(os.environ,{'METACULUS_DECISION_INPUT_PROFILE':'stable-v1'}):
            state=dict(evidence=[dict(source_id='s',evidence_id='e',url='u',body_sha256='h',start=0,end=1,text='x')],sources=[dict(source_id='s',url='u',body_sha256='h')])
            wire=model.request_payload(state,{})['state']
            self.assertEqual(wire['evidence'][0]['evidence_id'],'e');self.assertEqual(wire['sources'],state['sources'])
            self.assertNotIn('url',wire['evidence'][0])
            self.assertIn('Decisive primary',inputs.registry('binary',None)['evidence_sufficiency']['criteria'][-1])
            with patch.object(tokens,'available',return_value=True):self.assertEqual(tokens.policy(16384),{})
    def test_unknown_profile_fails_closed(self):
        with patch.dict(os.environ,{'METACULUS_DECISION_INPUT_PROFILE':'role-aware-v3'}):
            with self.assertRaisesRegex(ValueError,'UNKNOWN_DECISION_INPUT_PROFILE'):model.request_payload({}, {})

    def test_unconfigured_tokenizer_does_not_guess_token_count(self):
        with patch.dict(os.environ,{'METACULUS_DECISION_TOKENIZER_PATH':''}):
            self.assertIsNone(tokens.count({'text':'hello'}));self.assertEqual(tokens.policy(16384),{})
    def test_wrong_local_tokenizer_fails_before_parsing_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'tokenizer.json';p.write_text('{}')
            with patch.dict(os.environ,{'METACULUS_DECISION_TOKENIZER_PATH':str(p)}):
                with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):tokens.available()
    def test_provider_prompt_limit_takes_precedence_and_preserves_headroom(self):
        data={'data':dict(id=DECISION_MODEL,architecture={'output_modalities':['decisions']},endpoints=[dict(context_length=16384,max_prompt_tokens=10000,pricing=dict(prompt='0',completion='0'))])}
        with patch.object(tokens,'available',return_value=True):
            route=decision_route(opener=lambda *a,**k:io.StringIO(json.dumps(data)))
        self.assertEqual(route['context_length'],10000);self.assertEqual(route['second_token_limit'],5904);self.assertEqual(route['template_token_reserve'],4096)
    def test_compact_wire_preserves_text_and_traceable_ids_without_mutating_audit(self):
        state=dict(evidence=[dict(source_id='S1',evidence_id='E123',url='https://example.org',body_sha256='abc',start=10,end=30,text='The motion did NOT pass.')],sources=[dict(source_id='S1',url='https://example.org',body_sha256='abc',evidence_role='baseline',review_status='unverified')])
        before=copy.deepcopy(state);wire=model.request_payload(state,{})['state']
        self.assertEqual(wire['evidence'],[dict(source_id='S1',text='The motion did NOT pass.')]);self.assertEqual(wire['sources'][0]['review_status'],'unverified');self.assertEqual(state,before)
    def test_role_metadata_never_promotes_unverified_body(self):
        v=source('Example Alpha currently reports nine authorities.\n');p=packing.prepare(primitives.packet_for(v),v)
        self.assertEqual(p['sources'][0]['evidence_role'],'baseline');self.assertEqual(p['sources'][0]['review_status'],'unverified')
    def test_token_limit_rejects_base_and_call_before_http(self):
        with patch.object(tokens,'count',return_value=7000):
            v=source('Example Alpha body.\n');p=packing.prepare(primitives.packet_for(v),v)
            with self.assertRaisesRegex(ValueError,'BASE_TOKEN_LIMIT'):packing.select(p,spans.initial_state(p),24000,spans.questions(),token_limit=6000)
            with patch.object(model,'decide',side_effect=AssertionError('provider called')):
                with self.assertRaisesRegex(ValueError,'TOKEN_BOUND'):analysis.call({}, {},Path('unused'),lambda:None,24000,6000)
    def test_future_uncertainty_not_equated_to_no_useful_evidence(self):
        r=inputs.registry('binary',None)
        self.assertIn('future uncertainty',r['evidence_sufficiency']['criteria'][-1]);self.assertIn('parallel judgments',inputs.reading_policy())
    def test_literal_count_is_selected_source_scoped_and_hash_bound(self):
        from metaculus_agent.literal_calculations import selected_winner_labels
        text='British Grand Prix\nWinner:\nDriver A (Team)\nFrench Grand Prix\nWinner:\nDriver B (Team)\nCanadian Grand Prix\nWinner:\nDriver A (Team)\n'
        v=source(text,binding=False);v['request']['question']='How many drivers will win a Grand Prix?'
        p=packing.prepare(primitives.packet_for(v),v)
        records=[e for e in p['evidence'] if p['evidence_packing']['candidates'][e['evidence_id']]['kind']=='record_fact']
        self.assertEqual(len(records),3)
        aid=selected_winner_labels(p,{'evidence':records[:1]})[0]
        self.assertEqual(aid['distinct_label_count'],1);self.assertFalse(aid['complete_saved_record_selection'])
        aid=selected_winner_labels(p,{'evidence':records})[0]
        self.assertEqual(aid['distinct_label_count'],2);self.assertEqual(aid['record_count'],3)
        self.assertTrue(aid['complete_saved_record_selection']);self.assertIn('no entity resolution',aid['scope'])
        state,audit=packing.select(p,spans.initial_state(p),12943,spans.questions())
        self.assertEqual(state['literal_calculations'][0]['distinct_label_count'],2)
        self.assertLessEqual(audit['request_bytes'],12943)
    def test_material_opposing_original_sources_both_survive(self):
        v=source('Example Alpha regulator approved the change on October 1.\n',binding=False)
        import hashlib
        page=copy.deepcopy(v['pages']['https://example.org/data']);page['content']='Example Alpha regulator denied that the change was approved.\n';page['content_sha256']=hashlib.sha256(page['content'].encode()).hexdigest();page['capture_metadata']['text_sha256']=page['content_sha256'];v['pages']['https://example.org/correction']=page
        p=packing.prepare(primitives.packet_for(v),v);s,_=packing.select(p,spans.initial_state(p),12943,spans.questions());text=''.join(e['text'] for e in s['evidence']);self.assertIn('approved the change',text);self.assertIn('denied',text)

if __name__=='__main__':unittest.main()
