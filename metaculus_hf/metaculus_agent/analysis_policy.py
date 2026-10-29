"""Explicit paid decision route and safe provider failures, separate from evidence."""
import json,os,time
from decimal import Decimal
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from pipeline_v2.providers import SUPER

DECISION_MODEL='inception/mercury-decide'
# User-authorized paid route; allow the published undiscounted input rate.
# All prices are USD per token. No output or per-request surcharge is allowed.
MAX_PROMPT_PRICE=Decimal('0.00000004')

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
    prices=[]
    for row in endpoints:
        try:
            price={k:Decimal(str(row['pricing'][k])) for k in ('prompt','completion')}
            price['request']=Decimal(str(row['pricing'].get('request','0')))
            valid=(all(p.is_finite() and p>=0 for p in price.values()) and
                   price['prompt']<=MAX_PROMPT_PRICE and price['completion']==0 and price['request']==0)
        except (ArithmeticError,ValueError,KeyError,TypeError):valid=False
        if not valid:raise AnalysisProviderError('ANALYSIS_PRICE_LIMIT_EXCEEDED',category='configuration',retry_after=3600)
        prices.append(price)
    lengths=[r.get('context_length') for r in endpoints]
    if any(type(n)!=int or n<4096 for n in lengths):raise AnalysisProviderError('ANALYSIS_CONTEXT_LIMIT_UNKNOWN',category='configuration',retry_after=3600)
    # Respect a provider-specific prompt cap as well as its context window.
    for row in endpoints:
        n=row.get('max_prompt_tokens')
        if n is not None:
            if type(n)!=int or n<4096:raise AnalysisProviderError('ANALYSIS_CONTEXT_LIMIT_UNKNOWN',category='configuration',retry_after=3600)
            lengths.append(n)
    limit=min(lengths)
    from .decision_tokens import policy
    token_policy=policy(limit)
    result=dict(policy='mercury-paid-decision-v1',decision_model=selected,collection_model=SUPER,
        paid_route_authorized=True,pricing_verified_zero=all(p['prompt']==0 for p in prices),
        pricing_usd_per_token=[{k:str(v) for k,v in p.items()} for p in prices],
        max_prompt_usd_per_token=str(MAX_PROMPT_PRICE),
        protocol='typed-decisions',context_length=limit,first_byte_limit=min(22000,int(limit*.60)),second_byte_limit=min(28000,int(limit*.79)),
        byte_policy='conservative-UTF8-envelope-budget-with-template-headroom-not-exact-token-count',
        endpoint_count=len(endpoints),endpoint_names=[r.get('name') for r in endpoints],metadata_only_preflight=True,implicit_model_fallback=False)
    result.update(token_policy)
    if token_policy:result.update(first_byte_limit=24000,second_byte_limit=36000,byte_policy='dual-token-and-byte-budget-with-4096-template-reserve')
    result.update(final_byte_limit=result['second_byte_limit'],logical_decision_stage_cap=1)
    if 'second_token_limit' in result:result['final_token_limit']=result['second_token_limit']
    return result
