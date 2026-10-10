"""No-key public channels with bounded requests and native source identity."""
import json
import re
from datetime import datetime

CHANNELS = {
    'gdelt_news': dict(domain='news',role='news_discovery',format='json',endpoint='https://api.gdeltproject.org/api/v2/doc/doc',defaults={'mode':'artlist','format':'json','maxrecords':10,'sort':'datedesc','timespan':'1week'},parameters={'query':'Exact entities/phrases and GDELT operators; required','maxrecords':'1..50 article leads','timespan':'Relative interval, e.g. 1week; omit when supplying both UTC bounds','startdatetime':'UTC YYYYMMDDHHMMSS','enddatetime':'UTC YYYYMMDDHHMMSS'},records='gdelt',docs='https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/',caveat='News leads only, not original article bodies. seen date is an indexing timestamp, not verified publication or historical availability. The public query window may be limited.'),
    'dbnomics_series': dict(domain='finance',role='aggregated_statistics',format='json',endpoint='https://api.db.nomics.world/v22/series/{provider}/{dataset}/{series}',defaults={'observations':1},parameters={'provider':'Exact provider code, e.g. INSEE','dataset':'Exact dataset/release code; latest is not allowed','series':'Exact single series code; no wildcards'},path_parameters=['provider','dataset','series'],records='dbnomics',docs='https://docs.db.nomics.world/web-api/',caveat='Aggregated original-provider statistics. Preserve release, dimensions and units. Observation period is not publication vintage; latest revised data may leak later revisions.'),
    'bls_series': dict(domain='finance',role='official_statistics',format='json',endpoint='https://api.bls.gov/publicAPI/v1/timeseries/data/{series}',defaults={},parameters={'series':'Exact uppercase BLS series ID, e.g. CUUR0000SA0'},path_parameters=['series'],records='bls',docs='https://www.bls.gov/developers/api_signature.htm',caveat='No-key v1 single-series GET covers the provider default three-year window, not arbitrary historical years. Shared provider/IP quota: 25 queries per day. Preserve footnotes and M13 annual observations; units and seasonal adjustment require series documentation.'),
    'govinfo_feed': dict(domain='politics',role='official_document_discovery',format='feed',endpoint='https://www.govinfo.gov/rss/{collection}.xml',defaults={},parameters={'collection':'BILLS, PLAW, FR, CHRG, CREC or DCPD'},path_parameters=['collection'],records='feed',docs='https://www.govinfo.gov/feeds',caveat='Recent collection window only. Titles, links and summaries are not full government documents; explicitly download selected originals.'),
    'govinfo_text': dict(domain='politics',role='official_government_text',format='document',endpoint='https://www.govinfo.gov/content/pkg/{package}/html/{package}.htm',defaults={},parameters={'package':'Exact GovInfo package ID copied from official discovery, e.g. PLAW-119publ21'},path_parameters=['package'],records='document',docs='https://www.govinfo.gov/developers',caveat='Explicit HTML rendition; not every package has HTML. HTTP failures are retained, never treated as missing legal events. Preserve package identity and legislation stage; do not infer commencement.'),
}


def validate(source_id,params):
    if source_id not in CHANNELS: return
    if source_id=='gdelt_news':
        if not isinstance(params.get('query'),str) or not params['query'].strip(): raise ValueError('A nonempty GDELT query is required')
        if 'maxrecords' in params and (type(params['maxrecords']) is not int or not 1<=params['maxrecords']<=50): raise ValueError('GDELT maxrecords must be an integer in 1..50')
        bounds=[params.get(k) for k in ('startdatetime','enddatetime')]
        if any(bounds):
            if not all(isinstance(v,str) and re.fullmatch(r'[0-9]{14}',v) for v in bounds): raise ValueError('Both exact UTC bounds required')
            for value in bounds: datetime.strptime(value,'%Y%m%d%H%M%S')
            if bounds[0]>=bounds[1] or 'timespan' in params: raise ValueError('Ordered UTC bounds cannot combine with timespan')
        if 'timespan' in params and not re.fullmatch(r'[1-9][0-9]{0,2}(?:min|h|d|week|month)',str(params['timespan'])): raise ValueError('Invalid GDELT relative timespan')
    elif source_id=='dbnomics_series':
        for k in ('provider','dataset','series'):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,149}',str(params.get(k,''))): raise ValueError('Exact DBnomics identifiers required')
        if params['dataset'].split(':')[-1].lower()=='latest': raise ValueError('Freeze an exact dataset release instead of latest')
    elif source_id=='bls_series':
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,49}',str(params.get('series',''))): raise ValueError('Exact uppercase BLS series ID required')
    elif source_id=='govinfo_feed':
        if params.get('collection') not in {'BILLS','PLAW','FR','CHRG','CREC','DCPD'}: raise ValueError('Unsupported GovInfo collection')
    elif not re.fullmatch(r'(?:BILLS|PLAW|FR|CHRG|CREC|DCPD)-[A-Za-z0-9][A-Za-z0-9_-]{0,149}',str(params.get('package',''))):
        raise ValueError('Exact supported GovInfo package ID required')


def objects(value):
    if not isinstance(value,list) or any(not isinstance(x,dict) for x in value): raise ValueError('Expected native record objects')
    return value


def parse_channel(source,raw):
    body=json.loads(raw)
    if not isinstance(body,dict): raise ValueError('Expected public API JSON envelope')
    kind=source['records']; params=source.get('request_parameters',{})
    if kind=='gdelt':
        records=objects(body['articles'])
        for r in records:
            if not isinstance(r.get('url'),str) or not isinstance(r.get('title'),str): raise ValueError('Invalid GDELT lead')
        maximum=params.get('maxrecords',source['defaults']['maxrecords'])
        if len(records)>maximum: raise ValueError('GDELT returned more leads than requested')
        total=None; more=None
        metadata={k:v for k,v in body.items() if k!='articles'}
        metadata.update(full_article_body=False,result_window_may_be_saturated=len(records)==maximum)
    elif kind=='bls':
        if body.get('status')!='REQUEST_SUCCEEDED': raise ValueError('BLS did not report request success')
        result=body['Results']
        if isinstance(result,list):
            if len(result)!=1: raise ValueError('Unexpected BLS result envelope')
            result=result[0]
        series=objects(result['series'])
        if len(series)!=1 or series[0].get('seriesID')!=params.get('series'): raise ValueError('BLS returned a different series')
        observations=objects(series[0]['data'])
        for r in observations:
            if not all(k in r for k in ('year','period','value')): raise ValueError('Incomplete BLS observation')
        records=[dict(r,_source_context={'series_id':series[0]['seriesID']}) for r in observations]
        metadata={k:v for k,v in body.items() if k!='Results'}
        metadata['series_metadata']={k:v for k,v in series[0].items() if k!='data'}
        metadata['publication_vintage_verified']=False
        total=len(records); more=False
    else:
        envelope=body['series']; docs=objects(envelope['docs'])
        if len(docs)>1: raise ValueError('Exact DBnomics query returned multiple series')
        records=[]
        for doc in docs:
            identity=(doc.get('provider_code'),doc.get('dataset_code'),doc.get('series_code'))
            if identity!=tuple(params.get(k) for k in ('provider','dataset','series')): raise ValueError('DBnomics series identity mismatch')
            periods=doc['period']; values=doc['value']
            if not isinstance(periods,list) or not isinstance(values,list) or len(periods)!=len(values): raise ValueError('DBnomics observation arrays differ')
            labels=doc.get('period_start_day')
            if labels is not None and (not isinstance(labels,list) or len(labels)!=len(periods)): raise ValueError('DBnomics period dates differ')
            context={k:v for k,v in doc.items() if k not in {'period','value','period_start_day'}}
            for i,(period,value) in enumerate(zip(periods,values)):
                r={'period':period,'value':value,'_source_context':context}
                if labels is not None: r['period_start_day']=labels[i]
                records.append(r)
        metadata={k:v for k,v in body.items() if k!='series'}
        metadata['series_envelope']={k:v for k,v in envelope.items() if k!='docs'}
        total=len(records); more=bool(envelope.get('num_found',len(docs))>len(docs))
    return records,{'total_reported':total,'more_available':more,'scope':'single public response; no automatic pagination or retries','native_metadata':metadata}
