"""Narrow official structured-source adapter. Unknown UI filters never broaden a query."""
import json,re,hashlib
from urllib.parse import urlsplit,parse_qs,urlencode
from .contracts import PipelineError,now
from .repository import ensure

POLICY='observed-clinicaltrials-api-v1'
BASE='https://clinicaltrials.gov/api/v2/studies'

def registry_request(url):
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.hostname not in {'clinicaltrials.gov','www.clinicaltrials.gov'} or parts.port not in (None,443) or parts.username:return None
    query=parse_qs(parts.query,keep_blank_values=True)
    if parts.path=='/search':
        # Do not silently discard any unknown constraint or duplicate field.
        if set(query)-{'viewType','intr','aggFilters'} or any(len(v)!=1 for v in query.values()):return None
        if not query.get('intr',[''])[0].strip() or query.get('aggFilters')!=['status:rec']:return None
        params={'format':'json','query.intr':query['intr'][0],'filter.overallStatus':'RECRUITING','countTotal':'true','pageSize':'20',
                'fields':'NCTId,BriefTitle,OverallStatus,InterventionName,LastUpdatePostDate'}
        return dict(url=BASE+'?'+urlencode(params),kind='search_first_page',params=params)
    match=re.fullmatch(r'/study/(NCT\d{8})/?',parts.path)
    if match and not query:return dict(url=BASE+'/'+match[1]+'?format=json',kind='study_record',params={'nct_id':match[1]})
    return None

def registry_observation(transport,original,request):
    from .acquisition import public_url
    public_url(request['url'])
    body,meta=transport.request('fetch','GET',request['url'],headers={'Accept':'application/json'},timeout=35)
    public_url(meta['final_url'])
    try:value=json.loads(body);text=body.decode('utf-8')
    except (ValueError,UnicodeError):raise PipelineError('REGISTRY_RESPONSE_FORMAT') from None
    ensure(isinstance(value,dict),'REGISTRY_RESPONSE_FORMAT')
    if request['kind']=='search_first_page':
        rows=value.get('studies');count=value.get('totalCount')
        ensure(isinstance(rows,list) and type(count)==int and count>=len(rows),'REGISTRY_RESPONSE_FORMAT')
        ensure(all(isinstance(r,dict) and isinstance(r.get('protocolSection'),dict) for r in rows),'REGISTRY_RESPONSE_FORMAT')
        ensure(all(r['protocolSection'].get('statusModule',{}).get('overallStatus')=='RECRUITING' for r in rows),'REGISTRY_STATUS_MISMATCH')
        ids=[r['protocolSection'].get('identificationModule',{}).get('nctId') for r in rows]
        ensure(all(isinstance(n,str) and re.fullmatch(r'NCT\d{8}',n) for n in ids) and len(set(ids))==len(ids),'REGISTRY_STUDY_IDENTITY')
        complete=not bool(value.get('nextPageToken'))
        ensure(not complete or len(rows)==count,'REGISTRY_COUNT_INCONSISTENT')
        coverage=dict(returned_records=len(rows),reported_total=count,record_page_complete=complete,
                      current_observation_only=True,count_not_a_future_result=True)
    else:
        ensure(value.get('protocolSection',{}).get('identificationModule',{}).get('nctId')==request['params']['nct_id'],'REGISTRY_STUDY_IDENTITY')
        coverage=dict(current_observation_only=True)
    return dict(source_url=original,final_url=meta['final_url'],requested_at=meta['requested_at'],captured_at=meta['captured_at'],extracted_at=now(),
        status='extracted',evidence_kind='full_text',extraction_version=POLICY,http_status=meta['http_status'],
        locator=dict(retrieval_via='official_structured_api',observed_page_url=original,api_url=request['url'],query_params=request['params'],
                     raw_response_sha256=hashlib.sha256(body).hexdigest(),response_text_is_exact_utf8=True,coverage=coverage,outgoing_links=[])),text
