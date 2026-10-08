"""Cross-issuer and fiscal-period counterexamples for saved-source coverage."""
import copy
import unittest

from ForecastAgent.market_pulse import source_coverage as c, numeric_binding, review
from ForecastAgent.market_pulse.saved_source_trial import augmented_state, comparison_quantiles


def bundle(issuer='Zeta', label='fiscal 2028 third quarter', extra='', url=None):
    text = (f'{issuer} reports quarterly results\n{issuer} today announced financial results '
        f'for its {label}. Quarterly revenue was $12.6 billion. '
        'Diluted earnings per share was $3.40, up 14 percent year over year, '
        'including a favorable impact of $0.20 from tariff refunds.\n\n'
        + extra + '\n\n' + 'The report preserves consolidated company financial results. ' * 8)
    url = url or f'https://www.{issuer.lower()}.com/quarterly-report'
    return {'request': {'id': 'fixture', 'issuer_label': issuer,
        'question': f'What will be earnings per share? ({issuer})',
        'resolution_criteria': f'* {issuer} Q4 FY2028 first quarterly GAAP diluted EPS release',
        'fine_print': '', 'background': '', 'question_type': 'numeric', 'unit': '$',
        'scaling': {'range_min': 0, 'range_max': 10, 'zero_point': None},
        'open_lower_bound': True, 'open_upper_bound': True},
        'pages': {url: {'content': text, 'documents': [], 'retrieved_at_utc': '2028-10-01T00:00:00Z'}}}


def selections(table):
    row = next(r for r in table['candidates'] if 'Diluted earnings per share was' in r['row'])
    token = next(n for n in row['numbers'] if n['raw'] == '3.40')
    return [{'token_id': token['token_id'], 'metric': 'diluted_eps', 'source_unit': 'USD_per_share',
        'unit_ref': row['refs'][0]['ref_id'], 'period_ref': row['refs'][0]['ref_id'],
        'period_text': 'fiscal 2028 third quarter', 'basis': 'unknown', 'role': 'actual'}]


class SavedReportCoverageTests(unittest.TestCase):
    def test_missing_saved_latest_quarter_is_found(self):
        audit = c.audit(bundle(), [])
        recent = next(s for s in audit['slots'] if s['role'] == 'immediately_preceding_quarter_actual')
        self.assertEqual(recent['period'], [3, 2028])
        self.assertEqual(recent['status'], 'saved_source_without_period_bound_fact')
        self.assertFalse(recent['fact_rows_exposed'])

    def test_unseen_issuer_and_year_need_no_configuration(self):
        b = bundle('Delta'); b['request']['resolution_criteria'] = '* Delta Q4 FY2028 diluted EPS'
        self.assertEqual(c.audit(b, [])['issuer'], 'Delta')
        packet, _ = c.extraction_packet(b, [])
        self.assertTrue(packet['financial_rows'])

    def test_calendar_date_never_establishes_fiscal_quarter(self):
        a = c.audit(bundle(label='quarter ended September 30, 2028'), [])
        self.assertIsNone(a['sources'][0]['reported_period'])
        self.assertFalse(a['calendar_to_fiscal_mapping_inferred'])

    def test_compare_period_is_not_a_current_report_label(self):
        b = bundle(label='quarter ended September 30, 2028',
            extra='Revenue grew compared with third quarter 2027. Net income increased to $9 billion '
                'compared with $7 billion in third quarter 2027.')
        self.assertIsNone(c.inventory(b)['sources'][0]['reported_period'])

    def test_short_fiscal_release_label_is_supported(self):
        b = bundle(label='quarter ended September 30, 2028', extra='Earnings Release FY28 Q3\n')
        self.assertEqual(c.inventory(b)['sources'][0]['reported_period'], [3, 2028])

    def test_split_financial_highlights_label_is_supported(self):
        b = bundle(label='quarter ended September 30, 2028', extra='Third Quarter 2028\nFinancial Highlights\n')
        self.assertEqual(c.inventory(b)['sources'][0]['reported_period'], [3, 2028])

    def test_target_guidance_cannot_date_current_actual_report(self):
        b = bundle(extra='Fourth Quarter 2028 Outlook\nWe expect fourth quarter 2028 revenue of $15 billion.')
        self.assertEqual(c.inventory(b)['sources'][0]['reported_period'], [3, 2028])

    def test_ambiguous_original_report_labels_stay_unresolved(self):
        b = bundle(extra='Fiscal 2028 second quarter historical consolidated results.')
        self.assertIsNone(c.inventory(b)['sources'][0]['reported_period'])

    def test_future_actual_is_not_requested_as_historical_supplement(self):
        b = bundle(label='fiscal 2028 fourth quarter')
        packet, table = c.extraction_packet(b, [])
        self.assertEqual(table['candidates'], [])
        self.assertFalse(packet['coverage']['future_target_actual_required'])

    def test_exact_binding_closes_fact_and_exposure_gap(self):
        b = bundle(); packet, table = c.extraction_packet(b, [])
        vs, ids, _ = c.append_bound(b, [], table, selections(table), packet)
        self.assertEqual(ids, ['V1']); self.assertEqual(vs[0]['normalized_value'], 3.4)
        a = c.audit(b, vs)
        self.assertEqual(next(s for s in a['slots'] if s['role'] == 'immediately_preceding_quarter_actual')['status'], 'covered')
        self.assertFalse(vs[0]['interpretation_independently_verified'])

    def test_date_known_fact_can_still_be_unexposed(self):
        b = bundle(); packet, table = c.extraction_packet(b, [])
        vs, _, _ = c.append_bound(b, [], table, selections(table), packet)
        a = c.audit(b, vs, library=[])
        self.assertEqual(next(s for s in a['slots'] if s['role'] == 'immediately_preceding_quarter_actual')['status'], 'fact_not_exposed')

    def test_old_fact_ids_and_values_are_not_rewritten(self):
        b = bundle(); packet, table = c.extraction_packet(b, [])
        vs, _, _ = c.append_bound(b, [], table, selections(table), packet)
        vs[0]['fact_id'] = 'V9'
        new = selections(table); row = table['candidates'][0]
        token = next(n for n in row['numbers'] if n['raw'] == '12.6')
        new[0].update(token_id=token['token_id'], metric='revenue', source_unit='USD_billions')
        before = copy.deepcopy(vs)
        out, ids, _ = c.append_bound(b, vs, table, new, packet)
        self.assertEqual(out[:len(vs)], before); self.assertEqual(ids, ['V10'])

    def test_nonliteral_or_wrong_period_is_rejected(self):
        b = bundle(); packet, table = c.extraction_packet(b, [])
        chosen = selections(table); chosen[0]['period_text'] = 'Q3 FY2028'
        with self.assertRaisesRegex(ValueError, 'literal original report label'):
            c.append_bound(b, [], table, chosen, packet)

    def test_actual_source_cannot_be_relabelled_guidance(self):
        b = bundle(); packet, table = c.extraction_packet(b, [])
        chosen = selections(table); chosen[0]['role'] = 'management_guidance'
        with self.assertRaisesRegex(ValueError, 'relabel'):
            c.append_bound(b, [], table, chosen, packet)

    def test_default_binding_minimum_remains_unchanged(self):
        b = bundle(); _, table = c.extraction_packet(b, [])
        with self.assertRaisesRegex(ValueError, 'selected facts required'):
            numeric_binding.bind(b, table, selections(table))

    def test_disclosed_component_is_preserved_without_subtraction(self):
        b = bundle(); packet, table = c.extraction_packet(b, [])
        vs, ids, _ = c.append_bound(b, [], table, selections(table), packet)
        prior = {**review.context_catalog(b, []), 'allowed_variable_ids': [], 'other_field': 'frozen'}
        state = augmented_state(b, vs, prior, packet, ids)
        self.assertEqual(state['variables'][0]['normalized_value'], 3.4)
        self.assertTrue(any('tariff refunds' in r['original_text'] for r in state['original_evidence_library']))
        self.assertEqual(state['other_field'], 'frozen')
        self.assertIn('No component is automatically subtracted', state['historical_disclosure_policy'])

    def test_cumulative_table_columns_are_withheld_not_dated_by_intro(self):
        b = bundle(extra='Three Months Ended Nine Months Ended\n'
            '| Diluted EPS | 3.40 | 8.80 |\n')
        packet, table = c.extraction_packet(b, [])
        self.assertTrue(packet['withheld_table_rows'])
        self.assertFalse(any('|' in r['row'] for r in table['candidates']))

    def test_independent_audit_and_execution_report_schemas_are_explicit(self):
        qs = {p: {'value': 1, 'state': 'recorded'} for p in ('0.1', '0.5', '0.9')}
        self.assertEqual(comparison_quantiles({'quantiles': qs}), qs)
        self.assertEqual(comparison_quantiles({'independent_quantiles': qs}), qs)
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            comparison_quantiles({'quantiles': qs, 'independent_quantiles': {}})


if __name__ == '__main__':
    unittest.main()
