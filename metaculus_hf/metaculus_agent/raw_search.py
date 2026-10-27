"""Archive search response entities without changing provider calls or adapters."""
import base64,contextlib,hashlib,json,threading,uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
import requests
from pipeline_v2.contracts import now,canonical
from pipeline_v2.providers import Transport
from pipeline_v2.repository import atomic_bytes

ENDPOINTS={'api.exa.ai':'exa','api.tavily.com':'tavily'}
HEADERS={'content-type','date','x-request-id','request-id','retry-after','content-encoding'}

def saved(root):
    rows=[json.loads(p.read_bytes()) for p in sorted((Path(root)/'search-raw').glob('*.json'))]
    validate(rows);return rows

def validate(records):
    if not isinstance(records,list):raise ValueError('RAW_SEARCH_RECORDS')
    for row in records:
        if row.get('schema')!='metaculus-search-raw/1':raise ValueError('RAW_SEARCH_SCHEMA')
        body=base64.b64decode(row['body_base64'],validate=True)
        if len(body)!=row['body_bytes'] or hashlib.sha256(body).hexdigest()!=row['body_sha256']:raise ValueError('RAW_SEARCH_BODY_HASH')
        if row['byte_exact'] and (not row['body_complete'] or row['credential_redacted']):raise ValueError('RAW_SEARCH_COMPLETENESS')

@contextlib.contextmanager
def installed(root):
    """Observe only existing Exa/Tavily search HTTP; never issue new requests."""
    folder=Path(root)/'search-raw';local=threading.local()
    original_request,original_send=Transport.request,requests.Session.send
    def send(session,request,**kwargs):
        active=getattr(local,'active',None)
        if active is None:return original_send(session,request,**kwargs)
        record,parts=active;record['requested_at']=now();record['dispatched']=True
        response=original_send(session,request,**kwargs)
        record['http_status']=response.status_code
        record['response_headers']={k.lower():v for k,v in response.headers.items() if k.lower() in HEADERS}
        iterate=response.iter_content
        def content(*args,**kw):
            for chunk in iterate(*args,**kw):
                if chunk:parts.append(chunk)
                yield chunk
            record['body_complete']=True;record['received_at']=now()
        response.iter_content=content
        return response
    def request(transport,kind,method,url,**kwargs):
        endpoint=urlsplit(url);provider=ENDPOINTS.get(endpoint.hostname)
        if kind!='search' or method!='POST' or endpoint.scheme!='https' or endpoint.path!='/search' or not provider:
            return original_request(transport,kind,method,url,**kwargs)
        payload=kwargs.get('payload') or {};headers=kwargs.get('headers') or {}
        secrets=[v for k,v in {**payload,**headers}.items() if k.lower() in {'api_key','x-api-key','authorization'} and isinstance(v,str) and v]
        safe_params={k:v for k,v in payload.items() if k.lower() not in {'api_key','authorization','token','access_token'}}
        identity=uuid.uuid4().hex;parts=[]
        record=dict(schema='metaculus-search-raw/1',attempt_id=identity,run_id=transport.run_id,provider=provider,
                    endpoint=endpoint.scheme+'://'+endpoint.netloc+endpoint.path,request_params=safe_params,
                    recorded_at=now(),requested_at=None,received_at=None,dispatched=False,http_status=None,
                    body_complete=False,response_headers={},body_representation='requests-decoded-http-entity')
        previous=getattr(local,'active',None);local.active=(record,parts)
        try:
            return original_request(transport,kind,method,url,**kwargs)
        except Exception as exc:
            record['error_code']=getattr(exc,'code',type(exc).__name__);raise
        finally:
            local.active=previous;body=b''.join(parts);redacted=False
            # Providers can echo credentials in an error; those bytes cannot be archived.
            for secret in secrets:
                for value in {secret.encode(),json.dumps(secret)[1:-1].encode()}:
                    if value and value in body:body=body.replace(value,b'[REDACTED]');redacted=True
            record.update(finished_at=now(),body_base64=base64.b64encode(body).decode(),body_bytes=len(body),
                          body_sha256=hashlib.sha256(body).hexdigest(),credential_redacted=redacted,
                          byte_exact=record['body_complete'] and not redacted)
            # Exclude secret-bearing headers and never retain request headers.
            for key,value in record['response_headers'].items():
                for secret in secrets:value=value.replace(secret,'[REDACTED]')
                record['response_headers'][key]=value
            atomic_bytes(folder/(record['recorded_at'].replace(':','')+'-'+identity+'.json'),canonical(record).encode())
    with patch.object(Transport,'request',request),patch.object(requests.Session,'send',send):yield
