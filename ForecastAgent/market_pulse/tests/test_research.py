"""Target compatibility and state separation across generic issuers and periods."""
import copy
import unittest
from ForecastAgent.market_pulse import consensus as c, research_inventory as inv, source_coverage
from ForecastAgent.market_pulse.tests.test_source_coverage import bundle, selections
from ForecastAgent.market_pulse import review


def package(**updates):
    rows = [['Metric', 'Q4 FY2028'], ['Revenue mean', '12.5'], ['EPS', '(0.20)'], ['Analysts', '24']]
    metadata = {'issuer': 'Zeta', 'url': 'https://example.org/consensus',
        'published_at_utc': '2028-09-28T00:00:00Z', 'captured_at_utc': '2028-10-01T00:00:00Z',
        'publication_time_evidence': 'source header', 'upstream_id': 'aggregator-one',
        'upstream_identity_evidence': 'Source credit in table caption'}
    mapping = {'row': 1, 'column': 1, 'expected_row_label': 'Revenue mean',
        'expected_column_label': 'Q4 FY2028', 'role': 'consensus_estimate',
        'metric': 'revenue', 'basis': 'not_applicable', 'source_unit': 'USD_billions',
        'statistic': 'mean', 'period_kind': 'fiscal_quarter', 'period_label': 'Q4 FY2028',
        'fiscal_period': [4, 2028]}
    mapping.update(updates)
    return c.import_grid(rows, metadata, [mapping])


class ConsensusTests(unittest.TestCase):
    def test_import_and_integrity(self):
        p = package(); c.validate(p)
        self.assertEqual(p['records'][0]['value'], 12.5e9)
        p['records'][0]['value'] = 1
        with self.assertRaises(ValueError): c.validate(p)

    def test_header_change_is_gap(self):
        self.assertFalse(package(expected_column_label='Q3 FY2028')['records'])

    def test_statistic_header_is_bound_when_supplied(self):
        self.assertFalse(package(statistic_header_row=0, expected_statistic_label='Median')['records'])

    def test_unknown_fiscal_label_never_inferred(self):
        p = package(period_kind='source_quarter_unmapped', fiscal_period=None)
        target = {'issuer': 'Zeta', 'metric': 'revenue', 'basis': 'not_applicable',
            'normalized_unit': 'USD', 'fiscal_period': [4, 2028]}
        a = c.assess(p['records'][0], target, as_of='2028-10-02T00:00:00Z')
        self.assertFalse(a['compatible'])

    def test_actual_column_is_not_consensus(self):
        self.assertFalse(package(role='actual')['records'])

    def test_negative_eps_supported(self):
        p = package(row=2, expected_row_label='EPS', metric='diluted_eps', source_unit='USD_per_share', basis='GAAP')
        self.assertEqual(p['records'][0]['value'], -.2)

    def test_metric_unit_mismatch(self):
        self.assertFalse(package(source_unit='count')['records'])

    def test_period_label_not_rewritten(self):
        self.assertFalse(package(period_label='Q3 FY2028')['records'])

    def test_fiscal_mapping_requires_fy_literal(self):
        p = package(); rows = p['original_grid']; rows[0][1] = 'Q4-2028'
        mapping = {'row': 1, 'column': 1, 'expected_row_label': 'Revenue mean',
            'expected_column_label': 'Q4-2028', 'role': 'consensus_estimate',
            'metric': 'revenue', 'basis': 'not_applicable', 'source_unit': 'USD_billions',
            'statistic': 'mean', 'period_kind': 'fiscal_quarter', 'period_label': 'Q4-2028', 'fiscal_period': [4, 2028]}
        self.assertFalse(c.import_grid(rows, p['metadata'], [mapping])['records'])

    def test_analyst_count_not_independent_sources(self):
        p = package(row=3, expected_row_label='Analysts', statistic='analyst_count', source_unit='count')
        self.assertEqual(p['records'][0]['value'], 24)
        self.assertEqual(c.lineage(p['records'] * 4)['identified_upstream_count'], 1)
        self.assertFalse(c.lineage(p['records'])['independence_verified'])

    def test_unknown_lineage_is_not_counted(self):
        r = package()['records'][0]; r['upstream_identity_evidence'] = None
        self.assertEqual(c.lineage([r])['identified_upstream_count'], 0)

    def test_publication_is_not_capture(self):
        r = package()['records'][0]; r['published_at_utc'] = None
        target = {k: r[k] for k in ('issuer', 'metric', 'basis', 'normalized_unit', 'fiscal_period')}
        self.assertIn('published_at_utc_unknown_or_invalid', c.assess(r, target, as_of='2028-10-02T00:00:00Z')['gaps'])

    def test_future_capture_and_wrong_issuer(self):
        r = package()['records'][0]
        target = {k: r[k] for k in ('issuer', 'metric', 'basis', 'normalized_unit', 'fiscal_period')}
        target['issuer'] = 'Delta'
        a = c.assess(r, target, as_of='2028-09-30T00:00:00Z')
        self.assertFalse(a['compatible']); self.assertFalse(a['as_of_eligible'])

    def test_stale_publication(self):
        r = package()['records'][0]
        target = {k: r[k] for k in ('issuer', 'metric', 'basis', 'normalized_unit', 'fiscal_period')}
        self.assertIn('publication_stale', c.assess(r, target, as_of='2029-01-02T00:00:00Z')['gaps'])

    def test_merged_table_explicit_gap(self):
        tables, gaps = c.html_tables('<table><tr><th colspan="2">Q4</th></tr><tr><td>EPS</td><td>1</td></tr></table>')
        self.assertFalse(tables); self.assertTrue(gaps)

    def test_html_preservation(self):
        raw = '<table><tr><th>Metric</th><th>Q4 FY2028</th></tr><tr><td>Revenue mean</td><td>12.5</td></tr></table>'
        p = package(); tables, _ = c.html_tables(raw)
        config = {'row': 1, 'column': 1, 'expected_row_label': 'Revenue mean', 'expected_column_label': 'Q4 FY2028',
            'role': 'consensus_estimate', 'metric': 'revenue', 'basis': 'not_applicable', 'source_unit': 'USD_billions',
            'statistic': 'mean', 'period_kind': 'fiscal_quarter', 'period_label': 'Q4 FY2028', 'fiscal_period': [4, 2028]}
        p = c.import_grid(tables[0]['rows'], p['metadata'], [config], raw_source=raw); c.validate(p)
        p['raw_source'] += 'changed'
        with self.assertRaises(ValueError): c.validate(p)

    def test_revision_series_and_duplicate_capture(self):
        a = package()['records'][0]; b = copy.deepcopy(a)
        b.update(record_id='next', published_at_utc='2028-10-01T00:00:00Z', value=13e9)
        changes = c.revisions([a, a, b])['changes']
        self.assertEqual(len(changes), 1); self.assertEqual(changes[0]['delta'], .5e9)


class InventoryTests(unittest.TestCase):
    def test_guidance_leaf_has_explicit_contract(self):
        b = bundle(); b['request'].update(question='What will Zeta guidance be in Q3 FY2028? (Revenue)',
            unit='Billion $', resolution_criteria='The midpoint of the guidance range for Q4 FY2028 in Zeta Q3 FY2028 earnings press release, rounded to nearest billion.')
        result = inv.audit(b, [])
        self.assertEqual(result['target_period'], [4, 2028])
        self.assertEqual(result['slots'][0]['status'], 'not_applicable')

    def test_revenue_consensus_not_eps_consensus(self):
        b = bundle(); result = inv.audit(b, [], consensus_packages=[package()], as_of='2028-10-02T00:00:00Z')
        self.assertFalse(result['consensus_assessments'][0]['compatible'])
        self.assertEqual(next(s for s in result['slots'] if s['role'] == 'target_consensus')['status'], 'not_found')

    def test_saved_parsed_exposed_distinct(self):
        b = bundle(); packet, table = source_coverage.extraction_packet(b, [])
        variables, _, _ = source_coverage.append_bound(b, [], table, selections(table), packet)
        self.assertEqual(inv.audit(b, [])['slots'][1]['status'], 'saved_unparsed')
        self.assertEqual(inv.audit(b, variables)['slots'][1]['status'], 'parsed_unexposed')
        library = review.context_catalog(b, variables)['original_evidence_library']
        result = inv.audit(b, variables, library=library)
        self.assertEqual(result['slots'][1]['status'], 'exposed')
        self.assertFalse(result['future_target_actual_required'])
        self.assertFalse(result['slots'][1]['latest_publicly_available_established'])

    def test_exposure_tampering_rejected(self):
        b = bundle(); packet, table = source_coverage.extraction_packet(b, [])
        variables, _, _ = source_coverage.append_bound(b, [], table, selections(table), packet)
        library = review.context_catalog(b, variables)['original_evidence_library']
        library[0]['original_text'] = 'altered'
        with self.assertRaises(ValueError): inv.audit(b, [], library=library)

    def test_keywords_only_are_unparsed_and_inputs_preserved(self):
        b = bundle(extra='Analyst consensus estimates are expected soon.'); original = copy.deepcopy(b)
        result = inv.audit(b, [])
        self.assertEqual(next(s for s in result['slots'] if s['role'] == 'target_consensus')['status'], 'saved_unparsed')
        self.assertEqual(b, original); self.assertEqual(result['provider_calls'], 0)


if __name__ == '__main__': unittest.main()
