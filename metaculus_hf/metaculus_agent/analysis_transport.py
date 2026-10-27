"""Preserve public production failover; persist each physical model attempt."""
from contextlib import contextmanager
import io,json,os,time
from pathlib import Path
from urllib.error import HTTPError,URLError
from unittest.mock import patch
from pipeline_v2.providers import Budget
from .vendor import provider_failover as native
from .analysis_policy import classify,AnalysisProviderError

@contextmanager
def installed(root,run_id,checkpoint,*,opener=None):
    from ForecastAgent.providers import decisions,ultra
    from . import decision_model
    primary=os.environ.get('OPENROUTER_API_KEY','');backup=os.environ.get('OPENROUTER_API_KEY2','')
    if not primary:raise RuntimeError('MISSING_ANALYSIS_MODEL_KEY')
    budget=Budget(Path(root));original=opener or decisions.urlopen;events=[]
    cooldowns=Path(root)/'control/analysis-route-cooldowns.json'
    def cache():return json.loads(cooldowns.read_text()) if cooldowns.exists() else {}
    def identity(req):
        try:return json.loads(req.data or b'{}').get('model','unknown')
        except ValueError:return 'unknown'
    def physical(req,*a,**kw):
        role='primary' if req.get_header('Authorization')=='Bearer '+primary else 'backup'
        try:budget.reserve('model',run_id,provider='openrouter',credential='analysis-'+role)
        except Exception as exc:
            if getattr(exc,'code',None) in {'DAILY_BUDGET_LIMIT','RUN_BUDGET_LIMIT'}:
                events.append(dict(model=identity(req),http_status=None,credential_role=role,code='ANALYSIS_LOCAL_BUDGET_LIMIT',category='quota',retry_after=3600))
            raise
        checkpoint()  # A quota reservation must be remote-durable BEFORE HTTP.
        model=identity(req)
        try:
            response=original(req,*a,**kw);events.append(dict(model=model,http_status=getattr(response,'status',200),credential_role=role));return response
        except HTTPError as exc:
            body=exc.read(16384);fault=classify(exc.code,body,exc.headers)
            events.append(dict(model=model,http_status=exc.code,credential_role=role,code=fault.code,category=fault.category,retry_after=fault.retry_after))
            if fault.category=='provider':
                state=cache();state[model]=dict(code=fault.code,http_status=exc.code,retry_at=time.time()+fault.retry_after);native._write(cooldowns,state)
            raise HTTPError(req.full_url,exc.code,exc.reason,exc.headers,io.BytesIO(body)) from None
        except (OSError,TimeoutError):
            events.append(dict(model=model,http_status=None,credential_role=role,code='ANALYSIS_TRANSPORT_UNAVAILABLE',category='provider',retry_after=120));raise
    transport=native.ProviderTransport(physical,{'openrouter':(primary,backup)},Path(root)/'analysis-provider-transport')
    transport.analysis_events=events
    def routed(req,*a,**kw):
        model=identity(req);blocked=cache().get(model,{})
        if blocked.get('retry_at',0)>time.time():
            events.append(dict(model=model,http_status=blocked.get('http_status'),code=blocked['code'],category='provider',cached=True,retry_after=max(1,int(blocked['retry_at']-time.time()))))
            raise AnalysisProviderError(blocked['code'],retry_after=events[-1]['retry_after'],status=blocked.get('http_status'))
        try:return transport(req,*a,**kw)
        except HTTPError as exc:
            body=exc.read(16384);fault=classify(exc.code,body,exc.headers)
            if fault.category in {'quota','configuration'}:raise fault from None
            raise HTTPError(req.full_url,exc.code,exc.reason,exc.headers,io.BytesIO(body)) from None
        except URLError:
            last=events[-1] if events else {}
            if last.get('category')=='quota':raise AnalysisProviderError(last['code'],category='quota',retry_after=last['retry_after']) from None
            raise AnalysisProviderError('ANALYSIS_TRANSPORT_UNAVAILABLE',retry_after=120) from None
    write=native._write
    def durable(path,value):write(path,value);checkpoint()
    with patch.object(native,'_write',durable),patch.object(decisions,'urlopen',routed),patch.object(ultra,'urlopen',routed),patch.object(decision_model,'urlopen',routed):yield transport
