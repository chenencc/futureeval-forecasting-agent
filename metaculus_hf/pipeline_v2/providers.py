"""Bounded provider transport. Only compact counters survive; no payload journals."""
from __future__ import annotations
import json
import os
import re
import time
from urllib.parse import urlsplit
from .contracts import PipelineError, canonical, now
from .repository import atomic_bytes, file_lock, ensure

SUPER="nvidia/nemotron-3.5-super-vl:free"
MERCURY="inception/mercury-decide:free"


def retry_delay(value):
    """Parse Retry-After without retaining its untrusted raw value."""
    import math
    from datetime import datetime,timezone
    from email.utils import parsedate_to_datetime
    if not isinstance(value,str):return None
    try:seconds=float(value)
    except ValueError:
        try:seconds=(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
        except (ValueError,TypeError,OverflowError):return None
    return max(0.,seconds) if math.isfinite(seconds) else None


def provider_error(value):
    """Whitelist machine error fields, never messages, raw bodies or flagged source text."""
    err=value.get('error') if isinstance(value,dict) else None
    if not isinstance(err,dict):return {}
    out={};code=err.get('code')
    if type(code)==int and 400<=code<=599:out['provider_error_status']=code
    elif isinstance(code,str) and re.fullmatch(r'[45][0-9]{2}',code):out['provider_error_status']=int(code)
    meta=err.get('metadata');meta=meta if isinstance(meta,dict) else {}
    for key in ('error_type','provider_name','provider_code'):
        val=meta.get(key)
        if isinstance(val,str) and re.fullmatch(r'[A-Za-z0-9_. /:-]{1,100}',val):out[key]=val
    return out


class Budget:
    """Persistent experiment counters shared by all local V2 processes, never per-key quotas."""
    def __init__(self,root,*,model_daily=1500,tool_daily=1000,per_run=100):
        from pathlib import Path
        shared=os.environ.get("FORECAST_V2_SHARED_BUDGET_ROOT")
        ensure(not shared or Path(shared).is_absolute(),"SHARED_BUDGET_ROOT_NOT_ABSOLUTE")
        self.root=Path(shared or root);self.path=self.root/"control"/"provider-budget.jsonl"
        self.once_namespace=os.environ.get("FORECAST_V2_ONCE_NAMESPACE","")
        ensure(not self.once_namespace or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}",self.once_namespace),"INVALID_ONCE_NAMESPACE")
        self.lock=self.root/".locks"/"provider-budget.lock"
        self.model_daily,self.tool_daily,self.per_run=model_daily,tool_daily,per_run

    def reserve(self,kind,run_id,*,deadline=None,provider=None,credential=None,once=None):
        if once and self.once_namespace:once=self.once_namespace+"/"+once
        while True:
            with file_lock(self.lock):
                state=json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else dict(days={},runs={},last_model_at=0)
                ensure(once is None or once not in state.get("once",{}),"ONCE_REQUEST_ALREADY_RESERVED")
                day=now()[:10];counts=state["days"].setdefault(day,{})
                ensure(counts.get(kind,0)<(self.model_daily if kind=="model" else self.tool_daily),"DAILY_BUDGET_LIMIT")
                runs=state["runs"].setdefault(run_id,{})
                ensure(sum(runs.values())<self.per_run,"RUN_BUDGET_LIMIT")
                delay=max(0,state["last_model_at"]+4.0-time.time()) if kind=="model" else 0
                if delay==0:
                    counts[kind]=counts.get(kind,0)+1;runs[kind]=runs.get(kind,0)+1
                    if kind=="model":state["last_model_at"]=time.time()
                    if once:state.setdefault("once",{})[once]=dict(at=now(),run_id=run_id,provider=provider,credential=credential)
                    if provider and credential:
                        usage=state.setdefault("key_usage",{}).setdefault(provider,{}).setdefault(credential,{})
                        usage[kind]=usage.get(kind,0)+1
                    atomic_bytes(self.path,(canonical(state)+"\n").encode("utf-8"));return
            ensure(deadline is None or time.monotonic()+delay<deadline,"REQUEST_DEADLINE")
            time.sleep(min(delay,.1))


class Transport:
    def __init__(self,budget,run_id,*,on_evidence=None,on_model=None,deadline=None):
        self.budget,self.run_id=budget,run_id
        self.on_evidence,self.on_model=on_evidence,on_model
        self.on_progress=None
        self.deadline=deadline or time.monotonic()+600
        self.usage=dict(model_requests=0,search_requests=0,fetch_requests=0,input_tokens=0,output_tokens=0,cost_usd=0)
        self.unknown_cost=False
        self.unpriced_model=0

    def request(self,kind,method,url,*,payload=None,headers=None,timeout=60,limit=8*1024*1024,provider=None,credential=None,once=None):
        import requests
        self.budget.reserve(kind,self.run_id,deadline=self.deadline,provider=provider,credential=credential,once=once)
        left=self.deadline-time.monotonic();ensure(left>0,"REQUEST_DEADLINE")
        field="model_requests" if kind=="model" else "search_requests" if kind=="search" else "fetch_requests"
        self.usage[field]+=1
        if kind=="model":self.unpriced_model+=1
        if kind=="search" or "api.tavily.com" in url:self.unknown_cost=True
        if self.on_progress:self.on_progress(self.actual_usage())
        request=requests.Request(method,url,json=payload,headers=headers).prepare()
        # Payload serialization happens before the wire clock; no model health ping counts as evidence.
        hook=self.on_model if kind=="model" else self.on_evidence
        if hook:hook()
        started=now()
        try:
            with requests.Session() as session:
                session.max_redirects=5
                with session.send(request,timeout=min(timeout,left),stream=True,allow_redirects=method=="GET") as response:
                    parts=[];size=0
                    for chunk in response.iter_content(65536):
                        size+=len(chunk);ensure(size<=limit,"RESPONSE_TOO_LARGE")
                        ensure(time.monotonic()<self.deadline,"REQUEST_DEADLINE")
                        parts.append(chunk)
                    body=b"".join(parts)
                    metadata=dict(requested_at=started,captured_at=now(),http_status=response.status_code,final_url=response.url,
                                  content_type=response.headers.get("Content-Type",""))
                    if provider=='openrouter':
                        metadata['retry_after_seconds']=retry_delay(response.headers.get('Retry-After'))
                        request_id=response.headers.get('X-Request-ID') or response.headers.get('X-OpenRouter-Request-ID')
                        if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',request_id):metadata['request_id']=request_id
                    if not response.ok:
                        if provider=='openrouter':
                            try:metadata.update(provider_error(json.loads(body)))
                            except (ValueError,UnicodeError):pass
                        error=PipelineError(f"HTTP_{response.status_code}")
                        error.metadata=metadata
                        raise error
                    return body,metadata
        except requests.RequestException as exc:
            error=PipelineError("TRANSPORT_ERROR",type(exc).__name__)
            error.metadata=dict(requested_at=started,captured_at=None,http_status=None,final_url=None)
            raise error from None

    def json(self,*args,**kwargs):
        raw,meta=self.request(*args,**kwargs)
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):
            error=PipelineError("INVALID_PROVIDER_JSON");error.metadata=meta;raise error from None
        return value,meta

    def account(self,response):
        usage=response.get("usage") or {}
        self.usage["input_tokens"]+=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self.usage["output_tokens"]+=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        if isinstance(usage.get("cost"),(float,int)):
            self.usage["cost_usd"]+=usage["cost"];self.unpriced_model=max(0,self.unpriced_model-1)
        else:self.unknown_cost=True
        if self.on_progress:self.on_progress(self.actual_usage())

    def actual_usage(self):
        return dict(self.usage,cost_usd=None if self.unknown_cost or self.unpriced_model else self.usage["cost_usd"])


class Router:
    def __init__(self,transport,key_names=("OPENROUTER2",),*,on_receipt=None):
        self.transport,self.key_names=transport,key_names
        self.on_receipt=on_receipt

    def call(self,endpoint,payload,*,transient_attempts=1):
        ensure(type(transient_attempts)==int and 1<=transient_attempts<=2,'INVALID_TRANSIENT_ATTEMPTS')
        if endpoint=='alpha/decisions' and payload.get('model')==MERCURY:
            from .decision_budget import guard
            guard(payload)
        configured=[(name,os.environ[name]) for name in self.key_names if os.environ.get(name)]
        ensure(bool(configured),"MISSING_OPENROUTER_KEY")
        # Auth failover and transient retry share a total attempt bound; they never multiply.
        limit=2 if transient_attempts==2 else len(configured);key_index=0;transients=0;receipts=[]
        if not hasattr(self,'retry_receipts'):self.retry_receipts=[]
        for attempt in range(limit):
            name,key=configured[key_index]
            try:
                value,meta=self.transport.json("model","POST","https://openrouter.ai/api/"+endpoint,payload=payload,
                    headers={"Authorization":"Bearer "+key,"Content-Type":"application/json","X-Title":"ForecastAgent V2 local trial"},timeout=90,limit=2*1024*1024,provider="openrouter",credential=name)
                ensure(isinstance(value,dict),'PROVIDER_RESULT_FORMAT')
                if value.get('error') is not None:
                    safe=provider_error(value);status=safe.get('provider_error_status');exc=PipelineError('HTTP_'+str(status) if status else 'PROVIDER_ERROR');exc.metadata={**meta,**safe}
                    if value.get('usage'):self.transport.account(value)
                    raise exc
                self.transport.account(value)
                safe={k:v for k,v in meta.items() if k in {'requested_at','captured_at','http_status','request_id'}}
                row=dict(endpoint=endpoint,attempt=attempt+1,credential=name,outcome='succeeded',wire_started=bool(safe.get('requested_at')),**safe);receipts.append(row);self.retry_receipts.append(row)
                if getattr(self,'on_receipt',None):self.on_receipt(dict(row))
                return value
            except PipelineError as exc:
                safe={k:v for k,v in getattr(exc,'metadata',{}).items() if k in {'requested_at','captured_at','http_status','retry_after_seconds','request_id','provider_error_status','error_type','provider_name','provider_code'}}
                row=dict(endpoint=endpoint,attempt=attempt+1,credential=name,outcome=exc.code,wire_started=bool(safe.get('requested_at')),**safe);receipts.append(row);self.retry_receipts.append(row);exc.retry_receipts=receipts
                if getattr(self,'on_receipt',None):self.on_receipt(dict(row))
                if attempt+1>=limit:raise
                if exc.code in {'HTTP_401','HTTP_402','HTTP_403','HTTP_429'} and key_index+1<len(configured):
                    key_index+=1;continue
                if exc.code not in {'HTTP_502','HTTP_503','HTTP_504'} or transients>=transient_attempts-1:raise
                delay=max(1.,safe.get('retry_after_seconds') or 0.)
                if delay>30 or time.monotonic()+delay+1>=self.transport.deadline:
                    row['retry_skipped']='wait_exceeds_budget';raise
                row['retry_delay_seconds']=delay;transients+=1
                time.sleep(delay)
        raise PipelineError("NO_ROUTER_RESULT")

    def chat(self,system,state,*,model=SUPER,max_tokens=3500,json_mode=False,json_schema=None,transient_attempts=1):
        prompt=canonical(state)
        ensure(len(prompt.encode("utf-8"))+len(system.encode("utf-8"))<=500000,"CONTEXT_LIMIT")
        payload=dict(model=model,messages=[dict(role="system",content=system),dict(role="user",content=prompt)],max_tokens=max_tokens,temperature=.1,reasoning={"enabled":False})
        if json_schema is not None:payload["response_format"]={"type":"json_schema","json_schema":{"name":"evidence_review","strict":True,"schema":json_schema}}
        elif json_mode:payload["response_format"]={"type":"json_object"}
        value=self.call("v1/chat/completions",payload,transient_attempts=transient_attempts) if transient_attempts!=1 else self.call("v1/chat/completions",payload)
        try:choice=value["choices"][0];text=choice["message"]["content"]
        except (KeyError,IndexError,TypeError):raise PipelineError("MODEL_FORMAT") from None
        usage=value.get('usage') or {};details=usage.get('completion_tokens_details') or {}
        diagnostics=dict(finish_reason=choice.get('finish_reason'),completion_tokens=usage.get('completion_tokens'),reasoning_tokens=details.get('reasoning_tokens'),content_characters=len(text) if isinstance(text,str) else None)
        if choice.get('finish_reason')=='length':
            error=PipelineError('MODEL_OUTPUT_TRUNCATED');error.diagnostics=diagnostics;raise error
        ensure(isinstance(text,str) and bool(text.strip()),"EMPTY_MODEL_CONTENT")
        text=text.strip()
        if text.startswith("```"):
            text=text.split("\n",1)[1].rsplit("```",1)[0].strip()
        start=text.find("{")
        ensure(start>=0,"MODEL_FORMAT")
        try:
            value,end=json.JSONDecoder(parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x))).raw_decode(text[start:])
            ensure(isinstance(value,dict),"MODEL_FORMAT")
            return value
        except ValueError:raise PipelineError("MODEL_FORMAT") from None
