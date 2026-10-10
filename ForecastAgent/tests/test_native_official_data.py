"""New official adapters through the real host menu, ledger and capture boundary."""
import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.tests.test_native_capabilities import task
from ForecastAgent.channels import contracts, selection, native
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.research_loop import state
from ForecastAgent.tools import capabilities
from ForecastAgent.tools.intelligence_box import core
from ForecastAgent.tools.intelligence_box.catalog import SOURCES

CONTACT = 'ForecastAgent fixture@example.org'


def response(url, data):
    raw = data.encode() if isinstance(data, str) else json.dumps(data).encode()
    return dict(raw=raw, status=200, final_url=url, content_type='application/json',
                response_headers={}, truncated=False)


def cases():
    forecast = {'type':'Feature', 'properties':{'generatedAt':'2026-10-10T00:00:00Z',
        'periods':[{'number':1,'startTime':'2026-10-10T00:00:00Z',
                    'endTime':'2026-10-10T06:00:00Z','temperature':50,'temperatureUnit':'F'}]}}
    grid = {'office':'LWX', 'x':97, 'y':71}
    yield 'eurostat_data', {'dataset':'nama_10_gdp','filters':'{"geo":"DE","time":"2025","unit":"CP_MEUR"}'}, {
        'class':'dataset','id':['geo','time','unit'],'size':[1,1,1],
        'dimension':{k:{'category':{'index':{v:0}}} for k,v in
                     [('geo','DE'),('time','2025'),('unit','CP_MEUR')]},'value':{'0':10},'status':{'0':'p'}}
    yield 'ecb_series', {'flow':'EXR','series':'M.USD.EUR.SP00.A','lastNObservations':1}, 'KEY,TIME_PERIOD,OBS_VALUE,UNIT,OBS_STATUS\nEXR.M.USD.EUR.SP00.A,2025-01,1.04,USD,P\n'
    yield 'nws_point', {'latitude':38.9,'longitude':-77.0}, {
        'type':'Feature','id':'https://api.weather.gov/points/38.9,-77.0',
        'properties':{'gridId':'LWX','gridX':97,'gridY':71,
                      'forecast':'https://api.weather.gov/gridpoints/LWX/97,71/forecast'}}
    yield 'nws_forecast', grid, forecast
    yield 'nws_hourly', grid, forecast
    yield 'nws_stations', grid, {'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{'stationIdentifier':'KDCA','name':'Fixture airport'}}]}
    yield 'nws_observation', {'station':'KDCA'}, {'type':'Feature','properties':{
        'station':'https://api.weather.gov/stations/KDCA','timestamp':'2026-10-10T00:00:00Z',
        'temperature':{'value':10,'unitCode':'wmoUnit:degC'}}}
    yield 'nws_alerts', {'area':'CA'}, {'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{'id':'fixture','sent':'2026-10-10T00:00:00Z',
                                      'event':'Fixture warning','expires':'2026-10-10T01:00:00Z'}}]}
    yield 'sec_companyfacts', {'cik':'0001318605'}, {'cik':1318605,'entityName':'Tesla',
        'facts':{'us-gaap':{'Revenues':{'label':'Revenue','units':{'USD':[
            {'start':'2025-01-01','end':'2025-03-31','filed':'2025-04-24',
             'val':1,'form':'10-Q','accn':'fixture'}]}}}}}


class NativeOfficialDataTests(unittest.TestCase):
    def test_all_new_sources_use_native_budget_raw_identity_and_saved_rows(self):
        for source_id, params, body in cases():
            with self.subTest(source_id=source_id), tempfile.TemporaryDirectory() as root:
                t = task(root)
                t.store_page('https://www.sec.gov/fixture-issuer', {'content':
                    'The observed issuer identifier is CIK: 0001318605. ' * 15})
                before = copy.deepcopy(t.budget())
                with patch.dict(os.environ, {'NWS_USER_AGENT':CONTACT,'SEC_USER_AGENT':CONTACT}), \
                        patch.object(core, 'transport', side_effect=lambda url,*a,**kw: response(url,body)) as wire:
                    menu = t.execute('intelligence_catalog', {}, '')
                    selected = next(s for s in menu['sources'] if s['id'] == source_id)
                    self.assertEqual(selected['availability']['status'], 'available')
                    self.assertTrue(selected['parameter_guidance'])
                    out = t.execute('intelligence_fetch', {
                        'source_id':source_id, 'parameters':params, 'need_ids':['revenue']}, '')
                    self.assertEqual(out['status'], 'usable')
                    self.assertEqual(wire.call_count, 1)
                    self.assertEqual(len(t.bundle['fetch_attempts']), 1)
                    self.assertEqual(t.budget()['page_fetch_remaining'], before['page_fetch_remaining']-1)
                    self.assertEqual(t.bundle['searches'], [])
                    self.assertEqual(t.bundle['exa_searches'], [])
                    page = t.bundle['pages'][out['request_url']]
                    self.assertEqual(page['rows'], out['records'])
                    self.assertEqual(page['sha256'], out['raw_sha256'])
                    self.assertEqual(page['material_kind'], 'structured_observations')
                    self.assertEqual(page['source_id'], source_id)
                    material = state.catalog(t.bundle)
                    self.assertIn(out['request_url'], material['sources'])
                    self.assertTrue(any(s['url'] == out['request_url'] for s in material['spans'].values()))
                    self.assertEqual(out['budget_authority'], 'native_fetch_attempts')
                    if source_id.startswith(('nws_', 'sec_')):
                        self.assertEqual(wire.call_args.kwargs['user_agent'], CONTACT)
                    if source_id == 'sec_companyfacts':
                        self.assertEqual(wire.call_args.args[1], 8000000)
                        self.assertEqual(page['rows'][0]['filed'], '2025-04-24')
                    if source_id == 'eurostat_data':
                        self.assertEqual(page['rows'][0]['status'], 'p')
                        self.assertEqual(page['rows'][0]['dimensions']['unit'], 'CP_MEUR')
                    if source_id == 'ecb_series':
                        self.assertEqual(page['rows'][0]['UNIT'], 'USD')

    def test_contact_menu_and_transport_use_the_same_validated_configuration(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            t = task(root)
            self.assertEqual(selection.availability(t,'nws_alerts')['status'], 'configuration_required')
            with patch.object(core,'transport',side_effect=AssertionError('No HTTP')) as wire:
                out = t.execute('intelligence_fetch', {'source_id':'nws_alerts',
                    'parameters':{'area':'CA'},'need_ids':['revenue']}, '')
                self.assertEqual(out['status'], 'configuration_required')
                self.assertEqual(len(t.bundle['fetch_attempts']), 0)
                wire.assert_not_called()
            # Matches the standalone toolbox: valid SEC contact may supply NWS;
            # an explicitly invalid NWS contact is not silently replaced.
            os.environ['SEC_USER_AGENT'] = CONTACT
            self.assertEqual(selection.availability(t,'nws_alerts')['status'], 'available')
            with patch.object(core,'transport',side_effect=lambda u,*a,**kw: response(u,{'type':'FeatureCollection','features':[]})) as wire:
                t.execute('intelligence_fetch', {'source_id':'nws_alerts',
                    'parameters':{'area':'CA'},'need_ids':['revenue']}, '')
                self.assertEqual(wire.call_args.kwargs['user_agent'], CONTACT)
            os.environ['NWS_USER_AGENT'] = 'invalid-contact'
            self.assertEqual(selection.availability(t,'nws_alerts')['status'], 'configuration_required')
            os.environ['SEC_USER_AGENT'] = 'invalid-contact'
            self.assertEqual(selection.availability(t,'sec_companyfacts')['status'], 'configuration_required')

    def test_empty_and_truncated_responses_are_not_readable_observations(self):
        for truncated in (False, True):
            with self.subTest(truncated=truncated), tempfile.TemporaryDirectory() as root, \
                    patch.dict(os.environ, {'NWS_USER_AGENT':CONTACT}):
                t = task(root)
                def wire(url,*a,**kw):
                    reply=response(url,{'type':'FeatureCollection','features':[]})
                    reply['truncated']=truncated
                    return reply
                with patch.object(core,'transport',side_effect=wire):
                    out=t.execute('intelligence_fetch', {'source_id':'nws_alerts',
                        'parameters':{'area':'CA'},'need_ids':['revenue']}, '')
                self.assertNotEqual(out['status'], 'usable')
                self.assertNotIn(out['request_url'], t.bundle['pages'])
                self.assertEqual(len(t.bundle['fetch_attempts']),1)
                self.assertTrue(t.bundle['failed_captures'])

    def test_whole_directory_restore_replays_without_a_second_request(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'NWS_USER_AGENT':CONTACT}):
            t=task(Path(root)/'parent')
            args={'source_id':'nws_point','parameters':{'latitude':38.9,'longitude':-77.0},'need_ids':['revenue']}
            body=next(body for name,params,body in cases() if name=='nws_point')
            with patch.object(core,'transport',side_effect=lambda u,*a,**kw:response(u,body)) as wire:
                first=t.execute('intelligence_fetch',args,'')
                shutil.copytree(t.directory,Path(root)/'restored')
                restored=RetrievalTask(Path(root)/'restored', t.bundle['request'])
                replay=restored.execute('intelligence_fetch',args,'')
                self.assertTrue(replay['cached'])
                self.assertEqual(first['raw_sha256'],replay['raw_sha256'])
                self.assertEqual(wire.call_count,1)
                self.assertEqual(restored.budget(),t.budget())

    def test_catalog_schema_and_code_identity_include_all_new_sources(self):
        new={name for name,_,_ in cases()}
        self.assertEqual(len(SOURCES),32)
        self.assertTrue(new <= set(capabilities.get('intelligence_fetch').definition['function']['parameters']['properties']['source_id']['enum']))
        identity=contracts.code_identity()
        self.assertIn('tools/intelligence_box/official_data.py', identity)
        self.assertIn('channels/configuration.py', identity)
        self.assertIn('weather -> NWS point lookup',native.guide())
        self.assertEqual(contracts.DONOR,'cc2fe12')
