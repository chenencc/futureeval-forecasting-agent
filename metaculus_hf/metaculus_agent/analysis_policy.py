"""Explicit free analysis route and safe provider failures, separate from bad evidence."""
import json,os,time
from decimal import Decimal
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from pipeline_v2.providers import SUPER

DECISION_MODEL='baseten/mammoth-1:free'

class AnalysisProviderError(Exception):
    provider_failure=True
    def __init__(self,code,*,category='provider',retry_after=300,status=None):
        self.code,self.category,self.retry_after,self.http_status=code,category,retry_after,status
        super().__init__(code)

def classify(status,body='',headers=None):
    try:error=json.loads(body).get('error',{});message=str(error.get('message','')).lower()
    except (ValueError,AttributeError):message=''
    if status in (401,403):return AnalysisProviderError('ANALYSIS_CREDENTIAL_INVALID' if status==401 else 'ANALYSIS_ACCESS_DENIED',category='configuration',retry_after=3600,status=status)
    if status in (402,429):
        daily='free-models-per-day' in message or 'daily' in message
        delay=(86400-int(time.time())%86400) if daily else 3600 if status==402 else 60
        try:delay=max(delay,min(3600,int((headers or {}).get('Retry-After','0'))))
        except (ValueError,TypeError):pass
        return AnalysisProviderError('ANALYSIS_QUOTA_EXHAUSTED' if daily or status==402 else 'ANALYSIS_RATE_LIMITED',category='quota',retry_after=delay,status=status)
    if status==404:
        policy=any(x in message for x in ['data policy','privacy','data region','zdr'])
        return AnalysisProviderError('ANALYSIS_ROUTING_POLICY_BLOCKED' if policy else 'ANALYSIS_ROUTE_UNAVAILABLE',category='configuration' if policy else 'provider',retry_after=3600 if policy else 300,status=status)
    if status>=500:return AnalysisProviderError('ANALYSIS_PROVIDER_UNAVAILABLE',retry_after=120,status=status)
    return AnalysisProviderError('ANALYSIS_REQUEST_INVALID',category='configuration',retry_after=3600,status=status)

def decision_route(*,opener=urlopen):
    selected=os.environ.get('METACULUS_DECISION_MODEL',DECISION_MODEL).strip()
    if selected!=DECISION_MODEL:raise AnalysisProviderError('ANALYSIS_MODEL_NOT_ALLOWLISTED',category='configuration',retry_after=3600)
    try:
        with opener(Request('https://openrouter.ai/api/v1/models/'+selected+'/endpoints'),timeout=30) as response:metadata=json.load(response)['data']
    except (HTTPError,URLError,OSError,ValueError,KeyError):raise AnalysisProviderError('ANALYSIS_CATALOG_UNAVAILABLE',retry_after=120) from None
    endpoints=metadata.get('endpoints',[])
    if metadata.get('id')!=selected or not endpoints:raise AnalysisProviderError('ANALYSIS_ROUTE_UNAVAILABLE',retry_after=300,status=404)
    if 'decisions' not in metadata.get('architecture',{}).get('output_modalities',[]):raise AnalysisProviderError('ANALYSIS_DECISION_PROTOCOL_UNSUPPORTED',category='configuration',retry_after=3600)
    for row in endpoints:
        try:free=all(Decimal(str(row['pricing'][k]))==0 for k in ['prompt','completion'])
        except (ArithmeticError,ValueError,KeyError,TypeError):free=False
        if not free:raise AnalysisProviderError('ANALYSIS_NONFREE_ROUTE_REFUSED',category='configuration',retry_after=3600)
    lengths=[r.get('context_length') for r in endpoints]
    if any(type(n)!=int or n<4096 for n in lengths):raise AnalysisProviderError('ANALYSIS_CONTEXT_LIMIT_UNKNOWN',category='configuration',retry_after=3600)
    limit=min(lengths)
    return dict(policy='mammoth-free-decision-v1',decision_model=selected,collection_model=SUPER,pricing_verified_zero=True,
        protocol='typed-decisions',context_length=limit,first_byte_limit=min(22000,int(limit*.60)),second_byte_limit=min(28000,int(limit*.79)),
        byte_policy='conservative-UTF8-envelope-budget-with-template-headroom-not-exact-token-count',
        endpoint_count=len(endpoints),endpoint_names=[r.get('name') for r in endpoints],metadata_only_preflight=True,implicit_model_fallback=False)
