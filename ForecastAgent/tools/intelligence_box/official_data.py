"""Bounded official statistics and weather acquisition; retain native semantics."""
import csv
import io
import json
import math
import re
from datetime import date
from urllib.parse import urlencode

CHANNELS = {}
def contract(name, domain, role, endpoint, parameters, kind, docs, caveat, defaults=None, paths=None):
    CHANNELS[name]=dict(domain=domain,role=role,format='json',endpoint=endpoint,parameters=parameters,defaults=defaults or {},path_parameters=paths or [],records=kind,docs=docs,caveat=caveat)

contract('eurostat_data','finance','official_statistics','https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}',
 {'dataset':'Exact dataset code','filters':'JSON object of exact dimension code/value strings, including geo and time or bounded time range','lang':'EN, FR or DE'},'eurostat',
 'https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/api-statistics',
 'Latest revised JSON-stat data, not historical publication vintage. Units, dimensions, missing cells and flags are retained. Large/asynchronous responses are not observations.',{'format':'JSON','lang':'EN'},['dataset'])
contract('ecb_series','finance','official_statistics','https://data-api.ecb.europa.eu/service/data/{flow}/{series}',
 {'flow':'Exact dataflow, e.g. EXR','series':'Exact complete series key; no wildcards or OR','startPeriod':'Explicit first observation period','endPeriod':'Explicit last observation period','lastNObservations':'Alternative bounded last 1..100 observations'},'ecb',
 'https://data.ecb.europa.eu/help/api/data-examples',
 'SDMX CSV retains attributes and units. Observation period is not release date; revised data are not point-in-time vintage.',{'format':'csvdata'},['flow','series'])
base='https://api.weather.gov'
docs='https://www.weather.gov/documentation/services-web-api'
caveat='Forecasts, station observations and alerts are separate source roles. Preserve issue, valid, observation and capture times. No implicit linked requests or historical completeness.'
contract('nws_point','environment','weather_location',base+'/points/{latitude},{longitude}',{'latitude':'Latitude -90..90','longitude':'Longitude -180..180'},'nws_point',docs,caveat,paths=['latitude','longitude'])
contract('nws_forecast','environment','official_weather_forecast',base+'/gridpoints/{office}/{x},{y}/forecast',{'office':'Exact grid office from nws_point','x':'Nonnegative grid X','y':'Nonnegative grid Y'},'nws_forecast',docs,caveat,paths=['office','x','y'])
contract('nws_observation','environment','official_weather_observation',base+'/stations/{station}/observations/latest',{'station':'Exact observation station ID'},'nws_observation',docs,caveat,paths=['station'])
contract('nws_alerts','environment','official_weather_alert',base+'/alerts/active',{'area':'Exact two-letter area, e.g. CA','point':'Exact latitude,longitude; alternative to area'},'nws_alerts',docs,caveat)
contract('nws_stations','environment','weather_station_directory',base+'/gridpoints/{office}/{x},{y}/stations',{'office':'Exact office from point lookup','x':'Nonnegative grid X','y':'Nonnegative grid Y'},'nws_stations',docs,caveat,paths=['office','x','y'])
contract('nws_hourly','environment','official_weather_forecast',base+'/gridpoints/{office}/{x},{y}/forecast/hourly',{'office':'Exact office from point lookup','x':'Nonnegative grid X','y':'Nonnegative grid Y'},'nws_forecast',docs,caveat,paths=['office','x','y'])
for name in CHANNELS:
    if name.startswith('nws_'): CHANNELS[name]['requires_configuration']=['NWS_USER_AGENT']


def validate(name,p):
    if name=='eurostat_data':
        if not re.fullmatch(r'[a-z0-9_]{1,80}',str(p.get('dataset',''))): raise ValueError('Exact Eurostat dataset required')
        filters=json.loads(p.get('filters','{}'))
        if not isinstance(filters,dict) or not 1<=len(filters)<=15 or 'geo' not in filters: raise ValueError('Bounded exact Eurostat filters with geo required')
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,39}',k) or not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',v) for k,v in filters.items()): raise ValueError('Exact scalar dimension filters required')
        times=set(filters)&{'time','time_period','sinceTimePeriod','untilTimePeriod','lastTimePeriod'}
        if times not in ({'time'},{'time_period'},{'sinceTimePeriod','untilTimePeriod'},{'lastTimePeriod'}): raise ValueError('Explicit bounded Eurostat time selection required')
        if 'lastTimePeriod' in filters and (not filters['lastTimePeriod'].isdigit() or not 1<=int(filters['lastTimePeriod'])<=100): raise ValueError('Bound last time periods to 1..100')
        if set(filters)&{'format','lang','dataset'}: raise ValueError('Reserved controls are not dimension filters')
        if 'sinceTimePeriod' in filters and filters['sinceTimePeriod']>filters['untilTimePeriod']: raise ValueError('Ordered Eurostat bounds required')
        if p.get('lang','EN') not in {'EN','FR','DE'}: raise ValueError('Unsupported language')
    elif name=='ecb_series':
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,49}',str(p.get('flow',''))) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+',str(p.get('series',''))): raise ValueError('Exact ECB series without wildcard required')
        window=set(p)&{'startPeriod','endPeriod','lastNObservations'}
        if window=={'lastNObservations'}:
            if type(p['lastNObservations']) is not int or not 1<=p['lastNObservations']<=100: raise ValueError('Bound observations to 1..100')
        elif window=={'startPeriod','endPeriod'}:
            for key in window:
                if not re.fullmatch(r'[0-9]{4}(?:-(?:[0-9]{2}|[QM][1-9][0-9]?)(?:-[0-9]{2})?)?',str(p[key])): raise ValueError('Exact SDMX period required')
                if len(p[key])==10: date.fromisoformat(p[key])
            if p['startPeriod']>p['endPeriod']: raise ValueError('Ordered period bounds required')
        else: raise ValueError('Explicit bounded ECB window required')
    elif name=='nws_point':
        for key,lim in [('latitude',90),('longitude',180)]:
            v=p.get(key)
            if type(v) not in (int,float) or not math.isfinite(v) or not -lim<=v<=lim: raise ValueError('Finite geographic coordinate required')
    elif name in {'nws_forecast','nws_hourly','nws_stations'}:
        if not re.fullmatch(r'[A-Z]{3}',str(p.get('office',''))) or any(type(p.get(k)) is not int or not 0<=p[k]<=1000 for k in ('x','y')): raise ValueError('Exact NWS grid identity required')
    elif name=='nws_observation':
        if not re.fullmatch(r'[A-Z0-9]{3,12}',str(p.get('station',''))): raise ValueError('Exact NWS station required')
    elif name=='nws_alerts':
        if set(p)=={'area'}:
            if not re.fullmatch(r'[A-Z]{2}',p['area']): raise ValueError('Exact area required')
        elif set(p)=={'point'}:
            pair=p['point'].split(',')
            if len(pair)!=2: raise ValueError('Coordinate pair required')
            validate('nws_point',dict(latitude=float(pair[0]),longitude=float(pair[1])))
        else: raise ValueError('Select exactly one area or point')


def build(name,p):
    s=CHANNELS[name]; q=dict(s['defaults']); q.update({k:v for k,v in p.items() if k not in s['path_parameters']})
    if name=='eurostat_data': q.update(json.loads(q.pop('filters')))
    return s['endpoint'].format(**p)+('?' + urlencode(q) if q else '')


def parse_official(source,raw):
    kind=source['records']; p=source.get('request_parameters',{})
    if kind=='ecb':
        reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        required={'KEY','TIME_PERIOD','OBS_VALUE'}
        if not required.issubset(reader.fieldnames or []): raise ValueError('Incomplete ECB CSV columns')
        rows=list(reader)
        for row in rows:
            if row['KEY']!=p['flow']+'.'+p['series']: raise ValueError('ECB returned a different series')
            if row['TIME_PERIOD']<p.get('startPeriod',row['TIME_PERIOD']) or row['TIME_PERIOD']>p.get('endPeriod',row['TIME_PERIOD']): raise ValueError('ECB returned observations outside window')
            if not row['TIME_PERIOD']: raise ValueError('Missing observation period')
        if 'lastNObservations' in p and len(rows)>p['lastNObservations']: raise ValueError('ECB observation window exceeded')
        metadata={'columns':reader.fieldnames,'publication_vintage_verified':False}
    else:
        b=json.loads(raw)
        if not isinstance(b,dict) or 'error' in b or 'warning' in b: raise ValueError('Official API error or asynchronous envelope')
        if kind=='eurostat':
            ids=b['id']; sizes=b['size']; dims=b['dimension']
            if b.get('class')!='dataset' or len(ids)!=len(sizes) or len(set(ids))!=len(ids) or any(type(n) is not int or n<=0 for n in sizes): raise ValueError('Invalid JSON-stat dimensions')
            count=math.prod(sizes)
            if count>10000: raise ValueError('Narrow Eurostat query to at most 10000 cells')
            labels={}
            for dim,n in zip(ids,sizes):
                index=dims[dim]['category']['index']
                codes=index if isinstance(index,list) else [k for k,v in sorted(index.items(),key=lambda x:x[1])]
                if len(codes)!=n or (isinstance(index,dict) and sorted(index.values())!=list(range(n))): raise ValueError('Invalid category coordinates')
                labels[dim]=codes
            filters=json.loads(p['filters'])
            for key,value in filters.items():
                dim='time' if key=='time_period' else key
                if key not in {'sinceTimePeriod','untilTimePeriod','lastTimePeriod'} and (dim not in labels or labels[dim]!=[value]): raise ValueError('Eurostat returned different requested dimensions')
            times=labels.get('time',[])
            if 'lastTimePeriod' in filters and len(times)>int(filters['lastTimePeriod']): raise ValueError('Eurostat time count exceeded')
            if any(t<filters.get('sinceTimePeriod',t) or t>filters.get('untilTimePeriod',t) for t in times): raise ValueError('Eurostat time bounds exceeded')
            values=b.get('value',{}); flags=b.get('status',{})
            for cells in (values,flags):
                if isinstance(cells,list):
                    if len(cells)!=count: raise ValueError('Misaligned dense JSON-stat cells')
                elif not isinstance(cells,dict) or any(not str(k).isdigit() or not 0<=int(k)<count for k in cells): raise ValueError('Invalid sparse cells')
            cell=lambda obj,i: obj[i] if isinstance(obj,list) else obj.get(str(i))
            rows=[]
            for i in range(count):
                j=i; coords={}
                for dim,n in reversed(list(zip(ids,sizes))): coords[dim]=labels[dim][j%n]; j//=n
                rows.append(dict(dimensions=coords,value=cell(values,i),status=cell(flags,i)))
            metadata={k:v for k,v in b.items() if k not in {'value','status'}}
            metadata['publication_vintage_verified']=False
        else:
            props=b.get('properties')
            if kind in {'nws_alerts','nws_stations'}:
                if b.get('type')!='FeatureCollection' or not isinstance(b.get('features'),list): raise ValueError('Expected NWS feature collection')
                rows=b['features']; metadata={k:v for k,v in b.items() if k!='features'}
                if any(r.get('type')!='Feature' or not isinstance(r.get('properties'),dict) for r in rows): raise ValueError('Invalid alert feature')
            else:
                if not isinstance(props,dict) or b.get('type')!='Feature': raise ValueError('Expected NWS feature')
                metadata={k:v for k,v in b.items() if k!='properties'}
                if kind=='nws_forecast':
                    rows=props['periods']; metadata['properties']={k:v for k,v in props.items() if k!='periods'}
                    if not isinstance(rows,list) or any(not isinstance(r,dict) or not all(k in r for k in ('startTime','endTime','number')) for r in rows): raise ValueError('Invalid forecast periods')
                else: rows=[props]
                if kind=='nws_point' and b.get('id')!=f"https://api.weather.gov/points/{p['latitude']},{p['longitude']}": raise ValueError('NWS point identity mismatch')
                if kind=='nws_observation' and not str(props.get('station','')).endswith('/stations/'+p['station']): raise ValueError('NWS station identity mismatch')
            metadata['observation_not_forecast']=kind=='nws_observation'
    return rows,{'total_reported':len(rows),'more_available':bool(metadata.get('pagination',{}).get('next')),'scope':'single response; no automatic linked acquisition, pagination or retries','native_metadata':metadata}
