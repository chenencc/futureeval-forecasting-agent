import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.competition import mercury as primitives
from ForecastAgent.analysis import mercury_evidence_chain as spans
from metaculus_agent import evidence_packing as packing,decision_analysis,decision_model
from metaculus_agent.platform_adapter import live_request
from test_runner import post
from test_decision_model import response

def source(text,*,kind='binary',extra=None,binding=True):
    p=post(kind);request=live_request(p,p['question']);request['question']='How many Example Alpha authorities are Clear in 2030?'
    url='https://example.org/data';hashed=hashlib.sha256(text.encode()).hexdigest()
    pages={url:dict(content=text,content_sha256=hashed,retrieved_at_utc='2026-10-28T00:00:00Z',body_diagnostics=dict(usable_text=True),capture_metadata=dict(text_sha256=hashed))}
    if extra:pages.update(extra)
    start=text.find('| Clear |');start=0 if start<0 else start;end=text.find('\n',start)+1
    bindings={url:dict(inclusion='supporting',source_role='historical_baseline',forecast_relevance='baseline',relevance=dict(span=dict(start=start,end=end if end>start else len(text))))} if binding else {}
    return dict(request=request,pages=pages,gaps=[],source_bindings=bindings,original_reading=dict(unverified_source_ids=[]))

class PackingTests(unittest.TestCase):
    def prepare(self,value):return packing.prepare(primitives.packet_for(value),value)
    def select(self,packet,limit=9830):return packing.select(packet,spans.initial_state(packet),limit,spans.questions())
    def assert_exact(self,value,state):
        for e in state['evidence']:
            body=value['pages'][e['url']]['content'];self.assertEqual(body[e['start']:e['end']],e['text'])
            self.assertEqual(hashlib.sha256(body.encode()).hexdigest(),e['body_sha256'])
    def test_reviewed_late_table_keeps_header_units_date_note_and_original_bytes(self):
        text=('Navigation and general introduction without useful target observations.\n'*90)+'Table 1: Authorities, as of 2026-10-20, unit: countries\n| Status | Countries |\n| Clear | 9 |\n| Partial | 12 |\nNote: EEA countries are excluded.\n'
        value=source(text);before=copy.deepcopy(value);packet=self.prepare(value);state,audit=self.select(packet)
        delivered='\n'.join(e['text'] for e in state['evidence'])
        for required in ['| Clear | 9 |','| Status | Countries |','unit: countries','2026-10-20','Note: EEA countries are excluded.']:self.assertIn(required,delivered)
        self.assertGreater(audit['table_rows_selected'],0);self.assertLessEqual(packing.measure(state,spans.questions()),9830)
        self.assertEqual(before,value);self.assert_exact(value,state)
    def test_document_publication_and_wrapped_update_date_accompany_late_table(self):
        text='Example Alpha authority tracker\n08 Nov, 2024\nLast update:\n17 June 202\n6\n'+('Since 1 August 2024, this is a long explanatory paragraph about the background and purpose of national competent authorities. '*4)+'\n'+('General background introduction.\n'*90)+'Table 1: Authorities\n| Status | Count |\n| Clear | 9 |\nNote: Excludes EEA.\n'
        value=source(text);state,_=self.select(self.prepare(value));delivered=''.join(e['text'] for e in state['evidence'])
        self.assertIn('08 Nov, 2024',delivered);self.assertIn('17 June 202\n6\n',delivered);self.assertIn('| Clear | 9 |',delivered)
    def test_table_row_longer_than_budget_is_omitted_whole_never_truncated(self):
        row='| Alpha | '+('indivisible original cell '*180)+' |\n';value=source('Original Example Alpha report with readable independent context.\n\n| Name | Value |\n'+row,binding=False)
        state,audit=self.select(self.prepare(value));self.assertFalse(any('indivisible' in e['text'] for e in state['evidence']))
        self.assertEqual(audit['table_rows_selected'],0);self.assert_exact(value,state)
    def test_exact_duplicates_preserve_aliases_and_observation_clocks(self):
        value=source('Example Alpha official baseline: 9 Clear authorities in 2030.\n')
        page=copy.deepcopy(next(iter(value['pages'].values())));page['retrieved_at_utc']='2026-10-28T00:10:00Z'
        value['pages']['https://mirror.example.org/data']=page;packet=self.prepare(value)
        self.assertEqual(len(packet['sources']),1);group=packet['evidence_packing']['aliases'][0]
        self.assertEqual(len(group['urls']),2);self.assertEqual(len(group['observations']),2)
        self.assertEqual(group['exact_body_duplicate_count'],1);self.assertEqual(len(value['pages']),2)
    def test_changed_body_at_same_metric_is_not_near_duplicate_deleted(self):
        value=source('Example Alpha baseline: 9 Clear authorities as of October.\n');page=copy.deepcopy(next(iter(value['pages'].values())))
        page['content']='Example Alpha revised baseline: 10 Clear authorities as of November.\n';page['content_sha256']=hashlib.sha256(page['content'].encode()).hexdigest()
        value['pages']['https://example.org/revision']=page
        self.assertEqual(len(self.prepare(value)['sources']),2)
    def test_pending_or_missing_model_selection_uses_readable_original_body(self):
        value=source('Independent Example Alpha official report says nine authorities have been designated.\n',binding=False)
        state,audit=self.select(self.prepare(value));self.assertTrue(state['evidence']);self.assert_exact(value,state)
    def test_invalid_saved_review_span_never_creates_text_or_discards_body(self):
        value=source('Original Example Alpha report with a readable baseline.\n');value['source_bindings']['https://example.org/data']['relevance']['span']={'start':0,'end':999999}
        state,audit=self.select(self.prepare(value));self.assertTrue(state['evidence']);self.assert_exact(value,state)
    def test_hash_tampering_is_not_a_selection_fallback(self):
        value=source('Original Example Alpha report.\n');packet=primitives.packet_for(value);packet['sources'][0]['body_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):packing.prepare(packet,value)
    def test_readable_evidence_not_fitting_budget_does_not_become_question_prior(self):
        value=source('| Name | Value |\n| Alpha | '+('original long row '*500)+' |\n',binding=False);packet=self.prepare(value);base=packing.measure(spans.initial_state(packet),spans.questions())
        with self.assertRaisesRegex(packing.PackingError,'EVIDENCE_BUDGET'):self.select(packet,base+5)
    def test_complete_context_flag_cannot_overrun_exact_json_budget(self):
        value=source('Independent Example Alpha original baseline.\n',binding=False);packet=self.prepare(value)
        state,_=self.select(packet);size=packing.measure(state,spans.questions())
        with self.assertRaises(packing.PackingError):self.select(packet,size-1)
    def test_second_pass_keeps_first_exact_spans_and_adds_without_overrun(self):
        text=('Example Alpha baseline and independent original data in 2030. '*700)+'\n';value=source(text,binding=False);packet=self.prepare(value)
        first,a=self.select(packet);second,b=packing.select(packet,first,12943,spans.questions(),['observation_coverage'])
        self.assertTrue({e['evidence_id'] for e in first['evidence']}<{e['evidence_id'] for e in second['evidence']})
        self.assertLessEqual(b['request_bytes'],12943);self.assert_exact(value,second)
    def test_numeric_prior_compacts_model_grid_but_keeps_native_grid_and_payload(self):
        p=post('numeric');p['question']['forecast_grid']=[i/2 for i in range(201)];request=live_request(p,p['question'])
        value=dict(request=request,pages={},gaps=[],original_reading=dict(unverified_source_ids=[]))
        before=copy.deepcopy(value);states=[]
        def answer(state,registry,*args):states.append(copy.deepcopy(state));return response(registry)
        with tempfile.TemporaryDirectory() as tmp,patch.object(decision_model,'decide',side_effect=answer),patch.dict('os.environ',{'OPENROUTER_API_KEY':'fixture'}):
            result=decision_analysis.run(value,Path(tmp),dict(first_byte_limit=9830,second_byte_limit=12943),lambda:None)
        self.assertEqual(result['forecast_origin'],'question_prior');self.assertEqual(len(result['payload']['continuous_cdf']),201)
        self.assertNotIn('continuous_range',states[0]['question']['scaling']);self.assertEqual(value,before)

if __name__=='__main__':unittest.main()
