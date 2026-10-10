"""Official acquisition identities, temporal semantics and failure accounting."""
import json
import tempfile
import unittest
from ForecastAgent.tools.intelligence_box.core import Toolbox,build_url,parse,TOOL_DEFINITIONS
from ForecastAgent.tools.intelligence_box.catalog import SOURCES

class OfficialDataTests(unittest.TestCase):
    def decode(self,name,body,p):
        raw=body.encode() if isinstance(body,str) else json.dumps(body).encode()
        return parse(dict(SOURCES[name],request_parameters=p),raw)
    def euro(self):
        return dict(**{'class':'dataset'},id=['geo','time'],size=[1,2],dimension={'geo':{'category':{'index':{'DE':0},'label':{'DE':'Germany'}}},'time':{'category':{'index':{'2024':0,'2025':1}}}},value={'0':10},status={'1':'p'})
    def test_sparse_missing_flags_coordinates(self):
        rows,c=self.decode('eurostat_data',self.euro(),{'dataset':'x','filters':'{"geo":"DE","lastTimePeriod":"2"}'})
        self.assertEqual(rows[1],{'dimensions':{'time':'2025','geo':'DE'},'value':None,'status':'p'})
        self.assertFalse(c['native_metadata']['publication_vintage_verified'])
    def test_wrong_geo_and_cell_coordinates_rejected(self):
        with self.assertRaises(ValueError): self.decode('eurostat_data',self.euro(),{'filters':'{"geo":"FR"}'})
        b=self.euro();b['value']={'2':1}
        with self.assertRaises(ValueError): self.decode('eurostat_data',b,{'filters':'{"geo":"DE"}'})
    def test_async_not_observations(self):
        with self.assertRaises(ValueError): self.decode('eurostat_data',{'warning':{'status':413}}, {})
    def test_url_unpacks_filters_and_no_wildcards(self):
        url=build_url('eurostat_data',{'dataset':'nama_10_gdp','filters':'{"geo":"DE","time":"2025","unit":"CP_MEUR"}'})
        self.assertIn('geo=DE',url);self.assertNotIn('filters=',url)
        with self.assertRaises(ValueError): build_url('ecb_series',{'flow':'EXR','series':'M..EUR.SP00.A','lastNObservations':2})
    def test_ecb_attributes_missing_identity(self):
        params={'flow':'EXR','series':'M.USD.EUR.SP00.A','lastNObservations':2}
        text='KEY,TIME_PERIOD,OBS_VALUE,UNIT,OBS_STATUS\nEXR.M.USD.EUR.SP00.A,2025-01,,USD,P\n'
        rows,_=self.decode('ecb_series',text,params)
        self.assertEqual(rows[0]['OBS_VALUE'],'');self.assertEqual(rows[0]['OBS_STATUS'],'P')
        with self.assertRaises(ValueError): self.decode('ecb_series',text.replace('USD.EUR','GBP.EUR'),params)
    def test_nws_forecast_keeps_valid_times(self):
        row={'number':1,'startTime':'2026-10-10T00:00:00-04:00','endTime':'2026-10-10T06:00:00-04:00','temperature':50,'temperatureUnit':'F'}
        rows,c=self.decode('nws_forecast',{'type':'Feature','properties':{'generatedAt':'issued','periods':[row]}},{})
        self.assertEqual(rows,[row]);self.assertEqual(c['native_metadata']['properties']['generatedAt'],'issued')
        self.assertFalse(c['native_metadata']['observation_not_forecast'])
    def test_nws_station_identity(self):
        with self.assertRaises(ValueError): self.decode('nws_observation',{'type':'Feature','properties':{'station':'https://api.weather.gov/stations/KBOS'}},{'station':'KJFK'})
    def test_nws_empty_alerts_not_no_event_history(self):
        rows,c=self.decode('nws_alerts',{'type':'FeatureCollection','features':[],'pagination':{'next':'url'}},{'area':'CA'})
        self.assertEqual(rows,[]);self.assertTrue(c['more_available'])
    def test_invalid_parameters_no_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            box=Toolbox(root,max_requests=1,fetcher=lambda *_:self.fail('HTTP'))
            for name,p in [('nws_point',{'latitude':float('nan'),'longitude':1}),('nws_alerts',{}),('nws_forecast',{'office':'LWX','x':-1,'y':2}),('ecb_series',{'flow':'EXR','series':'M.USD.EUR.SP00.A'}),('eurostat_data',{'dataset':'x','filters':'{"geo":"DE"}'})]:
                with self.assertRaises(ValueError):box.fetch(name,p)
            self.assertEqual(box.budget()['reserved_attempts'],0);box.close()
    def test_nws_configuration_and_shared_cap_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            calls=[]
            def fetch(url,cap):calls.append(url);return dict(raw=b'{"type":"FeatureCollection","features":[]}',status=200,final_url=url,content_type='application/geo+json')
            box=Toolbox(root,max_requests=1,fetcher=fetch)
            self.assertEqual(box.fetch('nws_alerts',{'area':'CA'})['status'],'configuration_required')
            box.configuration['NWS_USER_AGENT']='ForecastAgent example@example.org'
            c=box.fetch('nws_alerts',{'area':'CA'});self.assertEqual(c['status'],'empty');box.close()
            box=Toolbox(root,max_requests=1,fetcher=fetch,configuration={'NWS_USER_AGENT':'ForecastAgent example@example.org'})
            self.assertTrue(box.fetch('nws_alerts',{'area':'CA'})['cache_hit'])
            with self.assertRaises(RuntimeError):box.fetch('ecb_series',{'flow':'EXR','series':'M.USD.EUR.SP00.A','lastNObservations':2})
            self.assertEqual(len(calls),1);box.close()
    def test_sec_companyfacts_preserves_period_and_concept(self):
        body={'cik':1318605,'entityName':'Tesla','facts':{'us-gaap':{'Revenues':{'label':'Revenue','units':{'USD':[{'start':'2025-01-01','end':'2025-03-31','filed':'2025-04-24','val':1,'form':'10-Q','accn':'id'}]}}}}}
        rows,_=self.decode('sec_companyfacts',body,{'cik':'0001318605'})
        self.assertEqual(rows[0]['_source_context']['tag'],'Revenues');self.assertEqual(rows[0]['filed'],'2025-04-24')

    def test_complete_companyfacts_uses_persisted_large_body_limit(self):
        with tempfile.TemporaryDirectory() as root:
            caps=[]
            def fetch(url,cap):
                caps.append(cap)
                return dict(raw=b'{"cik":1318605,"facts":{}}',status=200,final_url=url,content_type='application/json')
            box=Toolbox(root,max_requests=1,fetcher=fetch,configuration={'SEC_USER_AGENT':'ForecastAgent example@example.org'})
            self.assertEqual(box.fetch('sec_companyfacts',{'cik':'0001318605'})['status'],'empty')
            self.assertEqual(caps,[8000000]);box.close()
    def test_nws_station_directory_and_hourly_routes(self):
        self.assertEqual(build_url('nws_stations',{'office':'LWX','x':97,'y':71}),'https://api.weather.gov/gridpoints/LWX/97,71/stations')
        self.assertTrue(build_url('nws_hourly',{'office':'LWX','x':97,'y':71}).endswith('/forecast/hourly'))
        rows,_=self.decode('nws_stations',{'type':'FeatureCollection','features':[{'type':'Feature','properties':{'stationIdentifier':'KDCA','name':'Airport'}}]}, {})
        self.assertEqual(rows[0]['properties']['stationIdentifier'],'KDCA')
    def test_returned_time_bounds_rejected(self):
        with self.assertRaises(ValueError):self.decode('eurostat_data',self.euro(),{'filters':'{"geo":"DE","sinceTimePeriod":"2025","untilTimePeriod":"2026"}'})
