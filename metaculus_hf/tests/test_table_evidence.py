import copy
import unittest
from metaculus_agent import table_evidence as t, independent_review as review


SERIES='SEAS   YR  ANOM\nASO  2025 -0.78\nJAS  2026  1.69\n'
MARKDOWN='Q4 2025\n\n| Model | Production | Deliveries |\n| --- | --- | --- |\n| Model A | 420 | 400 |\n| Total | 440 | 418 |\n'


class TableEvidenceTests(unittest.TestCase):
    def test_whitespace_series_and_missing_target(self):
        x=t.parse(SERIES);self.assertEqual(len(x['tables']),1)
        self.assertEqual(t.bind(x,value='-0.78',period='ASO 2025',metric='ANOM')['status'],'bound')
        self.assertEqual(t.bind(x,value='-0.78',period='ASO 2026',metric='ANOM')['status'],'period_mismatch')
        self.assertEqual(t.bind(x,value='1.69',period='JAS 2026',metric='ANOM')['status'],'bound')
        self.assertEqual(t.bind(x,value='1.69',period='2026',metric='ANOM')['status'],'period_mismatch')
    def test_cross_row_cross_column_and_cross_table_rejected(self):
        x=t.parse(MARKDOWN+'\n2025\n\n| Model | Deliveries |\n| --- | --- |\n| Total | 1600 |\n')
        self.assertEqual(t.bind(x,value='418',period='Q4 2025',metric='Deliveries')['status'],'bound')
        self.assertEqual(t.bind(x,value='440',period='Q4 2025',metric='Deliveries')['status'],'metric_mismatch')
        self.assertEqual(t.bind(x,value='1600',period='Q4 2025',metric='Deliveries')['status'],'period_mismatch')
        self.assertEqual(t.bind(x,value='418',period='2025',metric='Deliveries')['status'],'period_mismatch')
    def test_exact_cell_ids_resolve_row_ambiguity(self):
        text='2025\n| Model | Deliveries |\n| --- | --- |\n| A | 10 |\n| B | 10 |\n'
        x=t.parse(text);self.assertEqual(t.bind(x,value='10',period='2025',metric='Deliveries')['status'],'ambiguous_cell')
        rec=list(t.records(x))[0]
        self.assertEqual(t.bind(x,value='10',period='2025',metric='Deliveries',cell_id=rec['cell_id'])['status'],'bound')
        self.assertEqual(t.bind(x,value='11',period='2025',metric='Deliveries',cell_id=rec['cell_id'])['status'],'value_mismatch')
    def test_offsets_bind_original_crlf_unicode_and_negative(self):
        text='气象数据\r\nSEAS YR ANOM\r\nASO 2025 −0.78\r\nJAS 2026 1.69\r\n'
        x=t.parse(text);bound=t.bind(x,value='-0.78',period='ASO 2025',metric='ANOM')
        self.assertEqual(bound['status'],'bound');self.assertEqual(bound['body_sha256'],t.sha(text))
        for cell in [bound['source_refs']['value']]+bound['source_refs']['headers']+bound['source_refs']['dimensions']:
            self.assertEqual(text[cell['start']:cell['end']],cell['raw'])
    def test_csv_and_quoted_thousands(self):
        text='Year,Metric,Value\n2025,"Revenue, digital","1,234.5"\n2026,"Revenue, digital",1300\n'
        x=t.parse(text);self.assertEqual(x['tables'][0]['layout'],'csv')
        b=t.bind(x,value='1234.5',period='2025',metric='Revenue, digital')
        self.assertEqual(b['status'],'bound');self.assertEqual(b['source_refs']['value']['raw'],'"1,234.5"')
    def test_tsv_empty_is_missing_not_zero(self):
        text='Year\tRegion\tValue\n2025\tA\t\n2026\tA\t0\n'
        x=t.parse(text);self.assertEqual(x['tables'][0]['rows'][0][2]['numeric']['status'],'missing')
        self.assertEqual(len(list(t.records(x))),1)
        self.assertEqual(t.bind(x,value='0',period='2025',metric='Value')['status'],'period_mismatch')
    def test_escape_pipe_preserves_source(self):
        text='2025\n| Product | Value |\n| --- | --- |\n| A \\| B | 10 |\n| C | 20 |\n'
        x=t.parse(text);r=list(t.records(x))[0]
        self.assertEqual(r['dimensions'][0]['value'],'A | B');self.assertIn('\\|',r['dimensions'][0]['raw'])
    def test_explicit_merged_headers(self):
        text='| Metric [rowspan=2] | 2025 [colspan=2] |\n| Q1 | Q2 |\n| Revenue | 10 | 20 |\n'
        x=t.parse(text);self.assertEqual(len(x['tables']),1)
        self.assertEqual(t.bind(x,value='20',period='Q2 2025',metric='Revenue')['status'],'bound')
        self.assertEqual(t.bind(x,value='20',period='Q1 2025',metric='Revenue')['status'],'period_mismatch')
    def test_merged_numeric_cells_not_assigned_to_multiple_periods(self):
        text='| Metric [rowspan=2] | 2025 [colspan=2] |\n| Q1 | Q2 |\n| Revenue | 10 [colspan=2] |\n'
        self.assertEqual(list(t.records(t.parse(text))),[])
    def test_invalid_span_and_ragged_rows_have_gaps(self):
        for text in ('| Metric [rowspan=9] | Value |\n| A | 1 |\n',
                     '| Metric | Value |\n| A | 1 | extra |\n'):
            x=t.parse(text);self.assertFalse(x['tables']);self.assertTrue(x['gaps'])
    def test_pdf_explicit_quarter_columns(self):
        text='(In millions)\nQ3 2025 Q2 2025 Q1 2025\nDigital revenue $ 98.1 $ 94.4 $ 70.9\nPrint revenue 34.1 35.2 36.3\n'
        x=t.parse(text);self.assertEqual(x['tables'][0]['layout'],'pdf_numeric')
        b=t.bind(x,value='98.1',period='Q3 2025',metric='Digital revenue',unit='$ millions')
        self.assertEqual(b['status'],'bound');self.assertEqual(b['unit_status'],'match')
        self.assertEqual(t.bind(x,value='94.4',period='Q3 2025',metric='Digital revenue')['status'],'period_mismatch')
    def test_flattened_multi_level_header_is_not_guessed(self):
        text='Third Quarter Nine Months\n2025 2024 % Change 2025 2024 % Change\nDigital revenues $ 98,111 $ 81,564 20.3 % $ 263,398 $ 224,166 17.5 %\n\n'
        x=t.parse(text);self.assertFalse(x['tables']);self.assertIn('TABLE_FLATTENED_GROUP_HEADERS_AMBIGUOUS',[g['code'] for g in x['gaps']])
        self.assertEqual(t.bind(x,value='98111',period='Q3 2025',metric='Digital revenues',span=dict(start=0,end=len(text)))['status'],'unresolved_layout')
    def test_unit_exceptions_remain_ambiguous(self):
        text='(In millions, except subscribers in thousands)\nQ3 2025 Q2 2025\nRevenue $ 98.1 $ 94.4\nSubscribers 200 190\n'
        b=t.bind(t.parse(text),value='98.1',period='Q3 2025',metric='Revenue',unit='$ millions')
        self.assertEqual(b['status'],'bound');self.assertEqual(b['unit_status'],'unresolved')
    def test_explicit_physical_unit_and_wrong_unit(self):
        text='Year\tMetric\tValue (mm)\n2025\tRain\t25\n2026\tRain\t30\n'
        x=t.parse(text)
        self.assertEqual(t.bind(x,value='25',period='2025',metric='Rain',unit='mm')['unit_status'],'match')
        self.assertEqual(t.bind(x,value='25',period='2025',metric='Rain',unit='cm')['status'],'unit_mismatch')
    def test_unit_column_not_inferred_from_metric(self):
        text='Year\tMetric\tUnit\tValue\n2025\tRain\tmm\t25\n2026\tRain\tmm\t30\n'
        x=t.parse(text)
        self.assertEqual(t.bind(x,value='25',period='2025',metric='Rain',unit='mm')['unit_status'],'match')
    def test_percent_not_scaled_and_dollar_not_assumed_usd(self):
        text='2025\n| Metric | Value |\n| --- | --- |\n| Growth | (7.1) % |\n| Revenue | $ 10 |\n'
        x=t.parse(text)
        b=t.bind(x,value='-7.1',period='2025',metric='Growth',unit='%')
        self.assertEqual(b['status'],'bound');self.assertEqual(b['record']['decimal'],'-7.1')
        self.assertEqual(t.bind(x,value='10',period='2025',metric='Revenue',unit='USD')['status'],'unit_mismatch')
        self.assertIsNone(t.bind(x,value='10',period='2025',metric='Revenue')['record']['unit']['currency'])
    def test_decimal_and_locale_bounds(self):
        self.assertEqual(t.number('.50')['decimal'],'0.50');self.assertEqual(t.number('(1,234.50)')['decimal'],'-1234.50')
        for value in ('1,23','1.234,50','NaN','inf','1e6',''):self.assertNotEqual(t.number(value)['status'],'numeric')
    def test_bounded_choices_are_deterministic_with_omissions(self):
        x=t.parse('SEAS YR ANOM\n'+'\n'.join('ASO '+str(y)+' 1.0' for y in range(2000,2026)))
        q=dict(question='ASO 2025 index');a=t.choices(x,q,3);self.assertEqual(a,t.choices(x,q,3));self.assertEqual(len(a['cells']),3);self.assertEqual(a['omitted_numeric_cells'],23)
        self.assertEqual(a['cells'][0]['period_components'],['2025','ASO'])
    def test_prose_and_plain_number_lists_not_structured(self):
        self.assertFalse(t.parse('The company reported revenue of 10 in 2025.\nThere are no aligned columns here.\n')['tables'])
        self.assertFalse(t.parse('1,2,3\n4,5,6\n7,8,9\n')['tables'])
    def test_body_limit_is_explicit(self):
        x=t.parse('x'*(t.MAX_BODY+1));self.assertFalse(x['complete_scan']);self.assertEqual(x['gaps'][0]['code'],'TABLE_BODY_LIMIT')
    def test_footnotes_and_unit_scope_are_retained_as_literal_dependencies(self):
        text='2025\n| Metric | Value |\n| --- | --- |\n| Sales (1) | 25 |\n\n(1) Excludes discontinued operations.\n'
        x=t.parse(text);b=t.bind(x,value='25',period='2025',metric='Sales (1)')
        self.assertEqual(len(b['source_refs']['notes']),1)
        note=b['source_refs']['notes'][0];self.assertEqual(text[note['start']:note['end']],note['raw'])
    def test_unknown_value_column_headers_do_not_create_fake_bindings(self):
        text='2025\n| Metric |  |\n| --- | --- |\n| Revenue | 10 |\n'
        x=t.parse(text);self.assertEqual(list(t.records(x)),[])
        self.assertIn('TABLE_MEASUREMENT_HEADERS_UNRESOLVED',[g['code'] for g in x['gaps']])
    def test_unrecognized_dense_layout_is_a_gap(self):
        text='Fiscal periods\n2025 2024 2023\nDigital revenues 10 20 30\nPrint revenues 20 30 40\n'
        x=t.parse(text);self.assertFalse(x['tables'])
        self.assertIn('TABLE_LIKE_LAYOUT_UNRESOLVED',[g['code'] for g in x['gaps']])
        self.assertEqual(t.bind(x,value='10',period='2025',metric='Digital revenues',span=dict(start=0,end=len(text)))['status'],'unresolved_layout')
    def test_dates_and_months_do_not_collapse_to_year_only(self):
        for first,second in [('2025-01-01','2025-02-01'),('January 2025','February 2025'),('1 May 2025','2 May 2025'),('2025-01-01T12:00Z','2025-01-01T13:00Z')]:
            text='Date\tMetric\tValue\n'+first+'\tRain\t25\n'+second+'\tRain\t30\n'
            x=t.parse(text)
            self.assertEqual(t.bind(x,value='25',period=first,metric='Rain')['status'],'bound')
            self.assertEqual(t.bind(x,value='25',period=second,metric='Rain')['status'],'period_mismatch')
            self.assertEqual(t.bind(x,value='25',period='2025',metric='Rain')['status'],'period_mismatch')
        self.assertEqual(t.period_tokens('Jan 2025'),t.period_tokens('January 2025'))
    def test_fiscal_periods_cannot_become_calendar_periods(self):
        self.assertNotEqual(t.period_tokens('FY2025'),t.period_tokens('2025'))


class TableReviewTests(unittest.TestCase):
    def fixture(self):
        q=dict(question='What is Rain in 2026?')
        body='Year\tMetric\tValue (mm)\n2025\tRain\t25\n2026\tRain\t30\n'
        d=dict(id='a',url='https://example.org',text=body,body_sha256=t.sha(body),complete=True)
        state=review.prepare(q,[d]);chosen=next(c for c in state['documents'][0]['table_choices']['cells'] if c['value_literal']=='25')
        claim=dict(citation_index=1,table_cell_id=chosen['cell_id'],interpretation='Rain 25 in 2025.',support='supported',entity_literal='Rain',metric_literal='Rain',period_literal='2025',value_literal='25',unit_literal='mm')
        result=dict(documents=[dict(id='a',relation='baseline',entity='match',metric='match',claims=[claim],gaps=[])])
        return state,result
    def test_join_success_and_wrong_year_block(self):
        s,x=self.fixture();c=review.assemble(s,x)['documents'][0]['claims'][0]
        self.assertEqual(c['table_binding']['status'],'bound');self.assertEqual(c['grade'],'bound_evidence')
        x['documents'][0]['claims'][0]['period_literal']='2026'
        c=review.assemble(s,x)['documents'][0]['claims'][0]
        self.assertEqual(c['table_binding']['status'],'period_mismatch');self.assertEqual(c['grade'],'unresolved')
    def test_old_claim_without_cell_id_also_checked(self):
        s,x=self.fixture();x['documents'][0]['claims'][0].pop('table_cell_id');x['documents'][0]['claims'][0]['period_literal']='2026'
        self.assertEqual(review.assemble(s,x)['documents'][0]['claims'][0]['grade'],'unresolved')
    def test_unoffered_cell_and_tampered_choices_block(self):
        s,x=self.fixture();x['documents'][0]['claims'][0]['table_cell_id']='invented'
        self.assertEqual(review.assemble(s,x)['documents'][0]['claims'][0]['table_binding']['status'],'cell_not_offered')
        s['documents'][0]['table_choices']['cells']=[]
        with self.assertRaisesRegex(ValueError,'CHOICES_CHANGED'):review.assemble(s,x)
        s,x=self.fixture();s['documents'][0]['table_choices']['cells'][0]['decimal']='999'
        with self.assertRaisesRegex(ValueError,'CHOICES_CHANGED'):review.assemble(s,x)
    def test_cell_id_without_numeric_value_cannot_pass(self):
        s,x=self.fixture();x['documents'][0]['claims'][0]['value_literal']=None
        self.assertEqual(review.assemble(s,x)['documents'][0]['claims'][0]['grade'],'unresolved')
    def test_source_and_prior_outputs_unchanged(self):
        s,x=self.fixture();a,b=copy.deepcopy(s),copy.deepcopy(x);review.assemble(s,x)
        self.assertEqual(s,a);self.assertEqual(x,b)


if __name__=='__main__':unittest.main()
