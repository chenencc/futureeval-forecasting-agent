"""Observe only saved sheet identities and keep partial/auth responses explicit."""
import base64,hashlib,json,unittest
from unittest.mock import patch
from ForecastAgent.market_repricing.acquisition_contract import (
    sheet_grid_profile,sheet_tab_leads,coverage,body_contract,failure_details,materialize_sheet,
    export_access_denied)

URL='https://docs.google.com/spreadsheets/d/book/edit?gid=1'


def page(rows=2227,loaded=100):
    tabs=[[21350203,json.dumps([0,0,'1',[{'1':[[0,0,'Daily Totals']]}],rows,13])],
          [21350203,json.dumps([1,0,'2',[{'1':[[0,0,'Monthly Totals']]}],75,19])]]
    tabs.append([341438337,json.dumps({'2':[{'1':['1',0,loaded,0,13]}]})])
    body='<html><body><script>var bootstrapData = '+json.dumps({'gridId':1,'changes':{'firstchunk':tabs}})+'; unsafeJavascript();</script></body></html>'
    raw=body.encode()
    return {'url':URL,'content':'Date | Value\n12/16/2020 | 4','content_type':'text/html',
       'raw_response_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),
       'retrieved_at_utc':'2026-10-14T00:00:00Z','charset':'utf-8'}


class SheetNavigation(unittest.TestCase):
    def rendered(self, rows=2):
        p=page(rows=rows,loaded=0)
        raw=base64.b64decode(p['raw_response_base64']).decode().replace('</body>',
          '<table class="waffle"><tr><th id="1R0">1</th><td>Month</td>'+ '<td></td>'*12+
          '</tr><tr><th id="1R1">2</th><td>Oct 2026</td><td colspan="12">44</td></tr></table></body>')
        p.update(raw_response_base64=base64.b64encode(raw.encode()).decode(),
           sha256=hashlib.sha256(raw.encode()).hexdigest(),retrieved_at_utc='2026-10-14T00:00:00Z')
        return p

    def test_rendered_coordinates_preserve_merges_and_month_precision(self):
        p=self.rendered();c=materialize_sheet(p,URL)
        self.assertEqual(c['sheet_cell_view']['rows'][1]['cells'][1]['address'],'B2')
        self.assertEqual(c['sheet_cell_view']['rows'][1]['cells'][1]['column_span'],12)
        self.assertEqual(c['sheet_cell_coverage']['latest_observed_month'],'2026-10')
        self.assertTrue(c['sheet_cell_coverage']['month_literal_cells'][0]['period_in_progress_at_capture'])
        self.assertTrue(c['sheet_cell_coverage']['all_declared_cells_rendered'])
        self.assertFalse(c['sheet_cell_coverage']['full_daily_series_verified'])
        self.assertEqual(c['raw_response_base64'],p['raw_response_base64'])
        self.assertEqual(materialize_sheet(c,URL),c)
        self.assertEqual(coverage(c,URL)['coverage_status'],'captured_rendered_grid_extent')

    def test_partial_rendered_grid_stays_partial(self):
        c=materialize_sheet(self.rendered(rows=2227),URL)
        self.assertFalse(c['sheet_cell_coverage']['all_declared_cells_rendered'])
        self.assertEqual(coverage(c,URL)['coverage_status'],'observed_subset')

    def test_vertical_merge_keeps_following_column_coordinates(self):
        p=self.rendered();raw=base64.b64decode(p['raw_response_base64']).decode()
        raw=raw.replace('<td>Month</td>','<td rowspan="2">Month</td>').replace('<td>Oct 2026</td>','')
        p.update(raw_response_base64=base64.b64encode(raw.encode()).decode(),sha256=hashlib.sha256(raw.encode()).hexdigest())
        c=materialize_sheet(p,URL);second=c['sheet_cell_view']['rows'][1]
        self.assertEqual(second['cells'][0]['address'],'B2')
        self.assertEqual(second['inherited_merged_cells'],{'1':'A1'})
        self.assertEqual(json.loads(json.dumps(c)),c)
        self.assertTrue(c['sheet_cell_coverage']['all_declared_cells_rendered'])

    def test_alternate_tab_export_does_not_repeat_workbook_auth_denial(self):
        denied='https://docs.google.com/spreadsheets/d/book/export?format=csv&gid=1'
        self.assertTrue(export_access_denied(denied.replace('gid=1','gid=2'),[{'url':denied,'http_status':401}]))
        self.assertFalse(export_access_denied(URL.replace('gid=1','gid=2'),[{'url':denied,'http_status':401}]))
        self.assertFalse(export_access_denied(denied.replace('/book/','/other/'),[{'url':denied,'http_status':401}]))

    def test_large_grid_loaded_extent_is_explicit(self):
        observed=sheet_grid_profile(page(),URL)
        self.assertEqual(observed['active_tab']['declared_rows'],2227)
        self.assertEqual(observed['declared_loaded_regions'][0]['row_end_exclusive'],100)
        self.assertFalse(observed['declared_grid_extent_loaded'])
        self.assertFalse(observed['javascript_executed'])

    def test_matching_small_grid_is_not_target_series_certification(self):
        c=coverage(page(rows=75,loaded=75),URL)
        self.assertEqual(c['coverage_status'],'captured_declared_grid_extent')
        self.assertFalse(c['complete_target_series_verified'])

    def test_wrong_tab_not_accepted_as_requested_view(self):
        c=coverage(page(),URL.replace('gid=1','gid=2'))
        self.assertEqual(c['coverage_status'],'requested_tab_not_returned')

    def test_navigation_retains_parent_and_exact_observed_gid(self):
        p=page();rows=sheet_tab_leads(p,URL)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['url'],URL.replace('gid=1','gid=2'))
        self.assertEqual(rows[0]['parent_source_sha256'],p['sha256'])
        self.assertEqual(rows[0]['observed_grid_id'],'2')

    def test_unknown_layout_never_invents_tab(self):
        p=page();p.pop('raw_response_base64')
        self.assertEqual(sheet_tab_leads(p,URL),[])

    def test_html_at_csv_route_is_not_original_csv(self):
        q=body_contract({'content':'Sign in to continue','content_type':'text/html'},
           URL.replace('/edit?gid=1','/export?format=csv&gid=1'),
           {'state':'readable','usable_text':True,'qualifies_full_body':True})
        self.assertFalse(q['usable_text']);self.assertEqual(q['state'],'csv_export_returned_html')

    def test_auth_failure_has_no_automatic_browser_route(self):
        exc=RuntimeError('Preserved child failure')
        exc.audit={'transport_error':{'detail':'HTTP Error 401: Unauthorized'}}
        d=failure_details(exc)
        self.assertEqual(d['failure_category'],'authentication_required')
        self.assertFalse(d['automatic_browser_retry_allowed'])


if __name__=='__main__':unittest.main()
