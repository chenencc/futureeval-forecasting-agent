"""Bounded acquisition, durable attempt reservations, and immutable evidence."""
from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .catalog import SOURCES, catalog


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class DocumentRejected(ValueError):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics=diagnostics


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None,443):
        raise ValueError("Only uncredentialed public HTTPS URLs are allowed")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or not all(ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Private network targets are forbidden")


class Redirects(HTTPRedirectHandler):
    def __init__(self, allowed_domains=None):
        super().__init__()
        self.allowed_domains=allowed_domains

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        if self.allowed_domains is not None and urlsplit(newurl).hostname not in self.allowed_domains:
            raise ValueError("Redirect host is not explicitly allowed")
        # A redirect is a second physical request. Return it to the agent so a
        # follow-up gets its own durable reservation and capture.
        return None


def transport(url, max_bytes, allowed_domains=None, user_agent=None, api_key=None):
    public_url(url)
    headers={"User-Agent":user_agent or "ForecastAgentIntelligenceToolbox/0.2", "Accept":"application/json,text/csv,application/xml,text/html,application/pdf"}
    if api_key:
        if urlsplit(url).hostname!='api.congress.gov': raise ValueError('Congress authentication is restricted to its exact API host')
        headers['X-Api-Key']=api_key
    request = Request(url, headers=headers)
    try:
        response = build_opener(Redirects(allowed_domains)).open(request, timeout=20)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read(max_bytes+1)
        return {"raw":raw[:max_bytes], "truncated":len(raw)>max_bytes, "status":response.code, "final_url":response.geturl(), "content_type":response.headers.get_content_type(), "retry_after":response.headers.get("Retry-After"),"redirect_location":response.headers.get("Location"),"response_headers":{k:response.headers[k] for k in ("Content-Encoding","Content-Type","ETag","Last-Modified","Date") if response.headers.get(k)}}


def build_url(source_id, params):
    source = SOURCES[source_id]
    if set(params)-set(source["parameters"]):
        raise ValueError("Unknown parameters; inspect the source catalog")
    if any(not isinstance(v,(str,int,float)) or len(str(v))>300 for v in params.values()):
        raise ValueError("Parameters must be bounded scalar values")
    from .public_channels import validate
    validate(source_id, params)
    paths = source.get("path_parameters", [])
    if source_id.startswith('congress_'):
        from .congress import validate_parameters
        validate_parameters(params)
    for key in paths:
        value = str(params.get(key,""))
        if not value or not all(c.isalnum() or c in ("._-:" if source_id=="dbnomics_series" else "._-") for c in value):
            raise ValueError("An exact safe path parameter is required: "+key)
    if source_id.startswith("sec_"):
        if not re.fullmatch(r"[0-9]{10}",str(params.get("cik",""))): raise ValueError("SEC CIK must have exactly ten digits")
        if source_id=="sec_concept" and params.get("taxonomy") not in {"us-gaap","ifrs-full","dei","srt"}: raise ValueError("Unsupported SEC taxonomy")
    if source_id in {"uk_bill_detail","uk_bill_publications"} and not re.fullmatch(r"[1-9][0-9]*",str(params.get("bill_id",""))): raise ValueError("Bill ID must be a positive integer")
    if source_id=="federal_register_detail" and not re.fullmatch(r"[0-9]{4}-[0-9]{5}",str(params.get("document_number",""))): raise ValueError("Invalid Federal Register document number")
    if source_id=="uk_legislation_xml":
        if params["type"] not in {"ukpga","uksi","ukla","asp","asc","anaw","mnia","nia","nisr"}: raise ValueError("Unsupported UK legislation type")
        if not re.fullmatch(r"[0-9]{4}",str(params["year"])) or not re.fullmatch(r"[1-9][0-9]*",str(params["number"])): raise ValueError("Exact statute year and number required")
        version=str(params.get("version",""))
        if version and version not in {"enacted","made"} and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",version): raise ValueError("Invalid statute version")
        return source["endpoint"].format(**params).replace("/data.xml",("/"+version if version else "")+"/data.xml")
    if source_id=='govinfo_feed':
        return source['endpoint'].format(collection=params['collection'].lower())
    if source_id == "fred_csv" and ("id" not in params or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_" for c in str(params["id"]))):
        raise ValueError("An exact single FRED series ID is required")
    endpoint = source["endpoint"].format(**{k:params[k] for k in paths})
    query = dict(source["defaults"], **{k:v for k,v in params.items() if k not in paths})
    if source_id=='gdelt_news' and 'startdatetime' in params: query.pop('timespan',None)
    return endpoint + ("?"+urlencode(query) if query else "")


def parse(source, raw):
    """Keep native records intact. Never synthesize an observation date."""
    fmt = source["format"]
    if fmt=='discovery':
        from .discovery import parse_discovery
        return parse_discovery(source,raw)
    if source.get("records") in {"gdelt", "dbnomics", "bls"}:
        from .public_channels import parse_channel
        return parse_channel(source,raw)
    total, more = None, None
    if fmt == "json":
        body = json.loads(raw)
        kind = source["records"]
        if kind.startswith('congress_'):
            from .congress import parse_envelope
            records,total,more,metadata=parse_envelope(kind,body)
        elif kind == "sec_concept":
            units=body["units"]
            if not isinstance(units,dict) or not isinstance(body.get("cik"),int): raise ValueError("Invalid SEC concept envelope")
            records=[]
            for unit,facts in units.items():
                if not isinstance(facts,list) or any(not isinstance(f,dict) for f in facts): raise ValueError("Invalid SEC unit facts")
                records.extend(dict(f,_source_context={"unit":unit,"cik":body["cik"],"taxonomy":body["taxonomy"],"tag":body["tag"],"entity_name":body.get("entityName")}) for f in facts)
            total,more=len(records),False
        elif kind == "sec_submissions":
            recent=body["filings"]["recent"]
            length=len(recent["accessionNumber"])
            if any(not isinstance(v,list) or len(v)!=length for v in recent.values()): raise ValueError("SEC filing columns have inconsistent lengths")
            records=[dict({k:v[i] for k,v in recent.items()},_source_context={"cik":body["cik"],"entity_name":body.get("name")}) for i in range(length)]
            total,more=length,bool(body["filings"].get("files"))
        elif kind == "object":
            if not isinstance(body,dict) or not body: raise ValueError("Expected a nonempty detail object")
            records=[body]
            total,more=1,False
        elif kind == "worldbank":
            if not isinstance(body,list) or len(body)!=2 or not isinstance(body[0],dict):
                raise ValueError("Unexpected World Bank envelope")
            records = body[1] or []
            total = int(body[0]["total"])
            more = int(body[0]["pages"]) > int(body[0]["page"])
        elif kind == "crossref":
            records = body["message"]["items"]
            total = body["message"].get("total-results")
        else:
            records = body[kind]
            total = body.get("count",body.get("totalCount",body.get("totalResults")))
            more = bool(body.get("nextPageToken") or body.get("next_page_url"))
        if not isinstance(records,list) or any(not isinstance(x,dict) for x in records):
            raise ValueError("Expected an array of record objects")
        if not kind.startswith('congress_'):
            metadata = body[0] if isinstance(body,list) else {k:v for k,v in body.items() if k not in {source["records"],"message","units"}}
        if kind=="sec_submissions": metadata["filings"]={"files":body["filings"].get("files",[])}
    elif fmt == "csv":
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or reader.fieldnames[0].lower() not in {"date","observation_date"}:
            raise ValueError("Missing observation date column; possible HTML shell")
        records = list(reader)
        total, more, metadata = len(records), False, {"columns":reader.fieldnames}
    elif fmt == "feed":
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("DTD and entity declarations forbidden")
        root = ET.fromstring(raw)
        local = lambda tag: tag.split("}")[-1]
        if local(root.tag) not in {"rss","feed","RDF"}:
            raise ValueError("Unexpected feed envelope")
        records=[]
        for item in root.iter():
            if local(item.tag) not in {"item","entry"}: continue
            record={}
            for child in item:
                key=local(child.tag)
                if key in {"title","description","summary","content","pubDate","published","updated","guid","id","link"}:
                    record[key]="".join(child.itertext()).strip() or child.attrib.get("href","")
            records.append(record)
        total, more, metadata = len(records), None, {"scope":"publisher feed window, not a full archive"}
    elif fmt == "clml":
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper(): raise ValueError("DTD and entity declarations forbidden")
        root=ET.fromstring(raw)
        namespace="http://www.legislation.gov.uk/namespaces/legislation"
        if root.tag!="{"+namespace+"}Legislation": raise ValueError("Expected official CLML legislation XML")
        records=[]
        count=0
        def walk(node,ancestors):
            nonlocal count
            context=ancestors+([node.attrib["id"]] if "id" in node.attrib else [])
            tag=node.tag.split("}")[-1]
            if tag in {"Text","Tabular"} and node.tag.startswith("{"+namespace+"}"):
                text=" ".join(" ".join(node.itertext()).split())
                if text:
                    count+=1
                    if len(records)<500: records.append({"text":text,"provision_ids":context,"element":tag,"original_xml":ET.tostring(node,encoding="unicode")})
                if tag=="Tabular": return
            for child in node: walk(child,context)
        walk(root,[])
        metadata={"document_uri":root.attrib.get("DocumentURI"),"root_attributes":root.attrib,"titles":[" ".join(n.itertext()).strip() for n in root.iter() if n.tag=="{http://purl.org/dc/elements/1.1/}title"],"changes_and_commencement_not_interpreted":True}
        total,more=count,count>len(records)
        if not records: raise ValueError("CLML contained no readable provisions")
    elif fmt == "document":
        from .documents import read_document
        page=read_document(source,raw)
        diagnostics=page.get("body_diagnostics",{})
        if page.get("parse_failure") or not page.get("content"):
            raise DocumentRejected("No readable document body; raw response preserved",dict(diagnostics,reader_failure=page.get("parse_failure")))
        if diagnostics.get("usable_text") is False:
            raise DocumentRejected("Reader rejected document body",diagnostics)
        records=page.get("documents",[])
        total,more,metadata=len(records),page.get("documents_truncated",False),{k:v for k,v in page.items() if k not in {"documents","raw_response_base64","content"}}
    else:
        raise ValueError("Unsupported source format")
    if total is not None and total>len(records): more=True
    coverage={"total_reported":total,"more_available":more,"scope":"single response; no automatic pagination","native_metadata":metadata}
    if fmt=='json' and source.get('records') in {'congress_actions','congress_texts'}:
        coverage['next_page_available']=bool(metadata.get('pagination',{}).get('next'))
    return records, coverage


class Toolbox:
    """The root is a single experiment/task ledger; reopening never resets caps."""
    def __init__(self, root, *, max_requests=30, fetcher=None, allowed_domains=(), configuration=None, max_document_bytes=8_000_000):
        if not isinstance(max_requests,int) or not 1<=max_requests<=1000:
            raise ValueError("max_requests must be an integer in 1..1000")
        if not isinstance(max_document_bytes,int) or not 1_000_000<=max_document_bytes<=32_000_000: raise ValueError("Document byte cap must be in 1..32 MB")
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        from .profiles import PROFILES
        self.allowed_domains={urlsplit(s["endpoint"]).hostname for s in SOURCES.values()} | {"www.govinfo.gov","www.congress.gov","www.sec.gov","bills.parliament.uk","publications.parliament.uk"} | set(allowed_domains) | {h for p in PROFILES.values() for h in p["hosts"]}
        self.configuration=dict(configuration or {})
        self.fetcher=fetcher or self._transport
        self.db=sqlite3.connect(self.root/"journal.sqlite", timeout=10)
        self.db.execute("CREATE TABLE IF NOT EXISTS config (cap INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, url TEXT, created TEXT, status TEXT, capture TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS document_policy (byte_cap INTEGER NOT NULL)")
        policy=self.db.execute("SELECT byte_cap FROM document_policy").fetchone()
        if policy and policy[0]!=max_document_bytes:
            self.db.close()
            raise ValueError("Existing document byte cap cannot change")
        if not policy: self.db.execute("INSERT INTO document_policy VALUES (?)",(max_document_bytes,))
        row=self.db.execute("SELECT cap FROM config").fetchone()
        if row and row[0]!=max_requests:
            self.db.close()
            raise ValueError("Existing ledger cap cannot change")
        if not row: self.db.execute("INSERT INTO config VALUES (?)",(max_requests,))
        self.db.commit()
        self.cap=max_requests
        self.max_document_bytes=max_document_bytes

    def close(self):
        self.db.close()

    def _sec_agent(self):
        value=self.configuration.get("SEC_USER_AGENT") or os.environ.get("SEC_USER_AGENT","")
        if not isinstance(value,str) or len(value)>200 or any(c in value for c in "\r\n") or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+",value): return None
        return value

    def _transport(self,url,limit):
        agent=self._sec_agent() if urlsplit(url).hostname in {"data.sec.gov","www.sec.gov"} else None
        if urlsplit(url).hostname=='api.congress.gov':
            return transport(url,limit,self.allowed_domains,user_agent='ForecastAgent public API validation',api_key=self._congress_key())
        return transport(url,limit,self.allowed_domains,user_agent=agent)

    def _congress_key(self):
        value=self.configuration.get('CONGRESS_API_KEY') or os.environ.get('CONGRESS_API_KEY','')
        return value if isinstance(value,str) and 0<len(value)<=512 and not any(c.isspace() for c in value) else None

    def budget(self):
        used=self.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        return {"maximum_http_attempts":self.cap,"reserved_attempts":used,"remaining":self.cap-used}

    def fetch(self, source_id, params=None, *, cache_max_age_seconds=900):
        params=params or {}
        source=SOURCES[source_id]
        url=build_url(source_id,params)
        ready=self._congress_key() if source_id.startswith('congress_') else self._sec_agent()
        if source.get("requires_configuration") and not ready:
            return {"version":"intelligence_capture_v1","source_id":source_id,"status":"configuration_required","configuration_names":source["requires_configuration"],"http_attempted":False,"records":[],"budget":self.budget()}
        return self._acquire(source_id,params,source,url,cache_max_age_seconds)

    def profile(self,profile_id,url=None,*,cache_max_age_seconds=900):
        from .profiles import resolve_profile
        profile,target=resolve_profile(profile_id,url)
        self._check_read_url(target)
        source={"domain":profile["domain"],"role":profile["role"],"format":"document","caveat":profile["caveat"],"profile":profile,"profile_id":profile_id}
        return self._acquire("profile:"+profile_id,{},source,target,cache_max_age_seconds)

    def discover(self,url,kind,profile_id=None,offset=0,limit=40,*,cache_max_age_seconds=900):
        from .discovery import KINDS
        from .profiles import resolve_profile
        if kind not in KINDS: raise ValueError('Unknown discovery kind')
        if type(offset) is not int or not 0<=offset<=100000: raise ValueError('offset must be in 0..100000')
        if type(limit) is not int or not 1<=limit<=100: raise ValueError('limit must be in 1..100')
        self._check_read_url(url)
        profile=None
        if profile_id: profile,url=resolve_profile(profile_id,url)
        if kind=='ir' and not profile: raise ValueError('Issuer discovery requires a curated profile_id')
        hosts=profile['hosts'] if profile else [urlsplit(url).hostname]
        params={'kind':kind,'profile_id':profile_id,'offset':offset,'limit':limit,'parser_version':'discovery_v1'}
        source={'format':'discovery','domain':profile['domain'] if profile else 'official_index',
                'role':'issuer_discovery' if profile else 'publisher_discovery',
                'discovery_parameters':params,'discovery_hosts':hosts,
                'caveat':'Discovery index is not full evidence or a complete archive. Linked originals require separate budgeted acquisition.'}
        return self._acquire('discovery:'+kind,params,source,url,cache_max_age_seconds)

    def acquire_link(self,capture_id,index,*,cache_max_age_seconds=900):
        from .discovery import parse_discovery
        from .profiles import PROFILES
        capture,raw=self._saved(capture_id)
        if not capture['source_id'].startswith('discovery:') or capture['status'] not in {'usable','empty'}:
            raise ValueError('Requires a successful discovery capture')
        if type(index) is not int or not 0<=index<len(capture['records']): raise ValueError('Invalid candidate index')
        params=capture['parameters']
        profile=PROFILES[params['profile_id']] if params.get('profile_id') else None
        source={'discovery_parameters':params,'final_url':capture['http']['final_url'],
                'discovery_hosts':profile['hosts'] if profile else [urlsplit(capture['request_url']).hostname],
                'response_headers':capture['http'].get('response_headers',{})}
        records,_=parse_discovery(source,raw)
        candidate=records[index]
        # Policy eligibility can tighten or expand after a curated host update;
        # the actual URL/label and source fields must still match original bytes.
        intrinsic=lambda record:{k:v for k,v in record.items() if k not in {'eligible','within_profile_hosts'}}
        if intrinsic(candidate)!=intrinsic(capture['records'][index]): raise ValueError('Candidate does not match saved raw index')
        if not candidate['eligible']: raise ValueError('Candidate host or credential policy rejected')
        if candidate['target_kind']=='sitemap': raise ValueError('Child sitemap requires an explicit discover call')
        url=candidate['url']; self._check_read_url(url)
        if urlsplit(url).hostname in {'data.sec.gov','www.sec.gov'} and not self._sec_agent():
            return {'status':'configuration_required','configuration_names':['SEC_USER_AGENT'],'http_attempted':False,'budget':self.budget()}
        binding={'parent_capture_id':capture_id,'parent_raw_sha256':capture['raw_sha256'],
                 'candidate_index':index,'candidate':candidate,'discovery_captured_at_utc':capture['captured_at_utc'],
                 'profile_id':params.get('profile_id'),'entity':profile['entity'] if profile else None,
                 'parent_candidate_eligible':capture['records'][index]['eligible'],
                 'current_allowed_hosts':source['discovery_hosts'],
                 'cik':profile.get('cik') if profile else None,'basis':'Saved official index link, not verified event or reporting-period equivalence'}
        source={'format':'document','domain':capture['domain'],'role':'discovered_original',
                'discovery_binding':binding,'caveat':'Linked original; index timestamp is not proof of publication time. Retain target-period and source applicability gaps.'}
        return self._acquire('discovered_original',{'parent_capture_id':capture_id,'index':index},source,url,cache_max_age_seconds)

    @staticmethod
    def _readable_original(capture):
        if capture.get('http',{}).get('status')!=200 or capture.get('http',{}).get('truncated'):
            raise ValueError('Offline extraction requires a complete HTTP 200 original')
        if capture.get('status') not in {'usable','empty'}:
            raise ValueError('Use explicit navigation/recovery for a failed or unparsed original')

    def links(self,capture_id,limit=40):
        from .profiles import PROFILES,inventory
        if type(limit) is not int or not 1<=limit<=100: raise ValueError("Link limit must be in 1..100")
        capture,raw=self._saved(capture_id)
        self._readable_original(capture)
        if capture["http"]["content_type"] not in {"text/html","application/xhtml+xml"}: raise ValueError("Link inventory requires a saved HTML response")
        profile=PROFILES.get(capture["source_id"].removeprefix("profile:"))
        return dict(inventory(raw,capture["http"]["final_url"],profile,limit),capture_id=capture_id,raw_sha256=capture["raw_sha256"],network_requests=0)

    def _saved(self,capture_id):
        if not isinstance(capture_id,str) or not re.fullmatch(r"[a-f0-9-]{36}",capture_id): raise ValueError("A saved capture UUID is required")
        capture=json.loads((self.root/"captures"/capture_id/"capture.json").read_text(encoding="utf-8"))
        path=(self.root/capture["raw_path"]).resolve()
        if not path.is_relative_to(self.root.resolve()): raise ValueError("Saved path is outside the task root")
        raw=path.read_bytes()
        if digest(raw)!=capture["raw_sha256"]: raise ValueError("Saved body hash mismatch")
        return capture,raw

    def tables(self,capture_id,max_rows=200):
        from .profiles import tables
        if type(max_rows) is not int or not 1<=max_rows<=1000: raise ValueError("Row limit must be in 1..1000")
        capture,raw=self._saved(capture_id)
        self._readable_original(capture)
        if capture["http"]["content_type"] not in {"text/html","application/xhtml+xml"}: raise ValueError("HTML table reading requires saved HTML")
        return dict(tables(raw,max_rows),capture_id=capture_id,raw_sha256=capture["raw_sha256"],network_requests=0)

    def provisions(self,capture_id,provision_id=None,max_chars=30000):
        if type(max_chars) is not int or not 500<=max_chars<=100000: raise ValueError("Provision text cap must be in 500..100000")
        capture,raw=self._saved(capture_id)
        self._readable_original(capture)
        if capture["source_id"]!="uk_legislation_xml": raise ValueError("Provision lookup requires a saved CLML capture")
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper(): raise ValueError("DTD and entity declarations forbidden")
        root=ET.fromstring(raw)
        if provision_id is None:
            entries=[{"id":n.attrib["id"],"element":n.tag.split("}")[-1],"text_preview":" ".join(" ".join(n.itertext()).split())[:200]} for n in root.iter() if "id" in n.attrib and n.tag.split("}")[-1] in {"P1","Schedule","Pblock"}]
            return {"capture_id":capture_id,"provisions":entries[:100],"total_provisions":len(entries),"index_truncated":len(entries)>100,"network_requests":0}
        if not isinstance(provision_id,str) or len(provision_id)>200: raise ValueError("A bounded exact provision ID is required")
        nodes=[n for n in root.iter() if n.attrib.get("id")==provision_id]
        result={"capture_id":capture_id,"raw_sha256":capture["raw_sha256"],"provision_id":provision_id,"network_requests":0,"status":"not_found" if not nodes else "ambiguous" if len(nodes)>1 else "found"}
        if len(nodes)==1:
            text=" ".join(" ".join(nodes[0].itertext()).split())
            original=ET.tostring(nodes[0],encoding="unicode")
            result.update(text=text[:max_chars],text_truncated=len(text)>max_chars,original_xml_excerpt=original[:max_chars],xml_excerpt_truncated=len(original)>max_chars)
        return result

    def read(self, url, *, cache_max_age_seconds=900):
        self._check_read_url(url)
        if urlsplit(url).hostname in {"data.sec.gov","www.sec.gov"} and not self._sec_agent():
            return {"version":"intelligence_capture_v1","source_id":"document","status":"configuration_required","configuration_names":["SEC_USER_AGENT"],"http_attempted":False,"records":[],"budget":self.budget()}
        if urlsplit(url).hostname=='api.congress.gov': raise ValueError('Use the typed Congress sources for authenticated API calls')
        source={"domain":"document","role":"unclassified_document","format":"document","caveat":"A readable document does not establish target relevance or truth. Scanned PDFs may need OCR; browser fallback is not automatic."}
        return self._acquire("document",{},source,url,cache_max_age_seconds)

    def bill_text(self,capture_id,version_index,format_index=0,*,detail_capture_id=None,cache_max_age_seconds=900):
        from .congress import select_text
        capture,raw=self._saved(capture_id)
        detail=self._saved(detail_capture_id)[0] if detail_capture_id else None
        url,binding=select_text(capture,version_index,format_index,detail)
        self._check_read_url(url)
        source={'domain':'politics','role':'official_bill_text','format':'document',
                'caveat':'Official text version is not proof of enactment or commencement. Preserve the selected version and original wording.',
                'congress_text_binding':binding}
        return self._acquire('congress_original_text',binding,source,url,cache_max_age_seconds)

    def _check_read_url(self,url):
        from urllib.parse import parse_qsl
        parts=urlsplit(url)
        if parts.hostname not in self.allowed_domains or parts.scheme!="https" or parts.username or parts.password or parts.port not in (None,443):
            raise ValueError("Document host must be explicitly allowed and public HTTPS")
        if any(any(s in k.lower() for s in ("key","token","secret","signature","password")) for k,_ in parse_qsl(parts.query)):
            raise ValueError("Credential-like query parameters are forbidden")

    def _acquire(self,source_id,params,source,url,cache_max_age_seconds):
        cache_warning=None
        # Cached evidence retains original capture time; failures are never cached.
        for (capture,) in self.db.execute("SELECT capture FROM attempts WHERE url=? AND status IN ('usable','empty','captured_unparsed') ORDER BY created DESC LIMIT 1",(url,)):
            try:
                saved=json.loads((self.root/capture).read_text(encoding="utf-8"))
                body_path=self.root/saved["raw_path"]
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(saved["captured_at_utc"])).total_seconds()
                if saved["source_id"]==source_id and saved.get('parameters')==params and 0<=age<=cache_max_age_seconds:
                    if digest(body_path.read_bytes())==saved["raw_sha256"]:
                        return dict(saved,cache_hit=True,budget=self.budget())
                    cache_warning="Cached raw body hash mismatch; fresh attempt required"
            except (OSError,ValueError,KeyError,TypeError):
                cache_warning="Cached capture is unreadable; fresh attempt required"
        attempt=str(uuid.uuid4())
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.budget()["remaining"]<=0: raise RuntimeError("HTTP attempt budget exhausted")
            self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,?)",(attempt,url,now(),"reserved",None))
        folder=self.root/"captures"/attempt
        folder.mkdir(parents=True)
        result={"version":"intelligence_capture_v1","id":attempt,"source_id":source_id,"domain":source["domain"],"role":source["role"],"request_url":url,"parameters":params,"captured_at_utc":now(),"status":"failed","records":[],"cache_hit":False,"quality":{"truth_verified":False,"target_relevance_verified":False,"full_article_body":False,"historical_vintage_verified":False},"limitations":[source["caveat"]],"analysis_started":False,"submitted":False}
        if cache_warning: result["limitations"].append(cache_warning)
        if source.get('discovery_binding'): result['source_binding']=source['discovery_binding']
        try:
            byte_cap=self.max_document_bytes if source["format"] in {"document","clml"} else 2_000_000
            result["byte_cap"]=byte_cap
            response=self.fetcher(url,byte_cap)
            raw=response.pop("raw")
            key=self._congress_key() if urlsplit(url).hostname=='api.congress.gov' else None
            if key and key.encode() in raw:
                raw=raw.replace(key.encode(),b'[REDACTED_CREDENTIAL]')
                result['raw_redacted']=True
                result['limitations'].append('Provider reflected a credential; saved response is redacted, not byte-identical to the wire response')
            body_path=folder/"response.raw"
            body_path.write_bytes(raw)
            result.update(http=response,raw_path=str(body_path.relative_to(self.root)),raw_sha256=digest(raw),raw_bytes=len(raw))
            if response["status"] != 200: raise ValueError("HTTP "+str(response["status"]))
            if response.get("truncated"): raise ValueError("Response exceeded byte cap; raw prefix preserved")
            result['quality']['original_download_complete']=True
            office_types={'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                          'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                          'application/msword','application/vnd.ms-excel','application/vnd.ms-powerpoint'}
            if source['format']=='document' and response['content_type'] in office_types:
                # Original acquisition is complete; no binary-to-text decoding
                # or hidden external document service is attempted.
                result.update(status='captured_unparsed',coverage={'total_reported':None,'more_available':None,
                    'scope':'complete downloaded original; document parser unavailable','native_metadata':{'content_type':response['content_type']}})
                result['quality'].update(readable_document=False,structure_valid=None)
                result['limitations'].append('Original file downloaded; Office document parsing is not implemented')
                return self._finish_capture(result,folder,attempt)
            parse_source=dict(source,request_parameters=params,url=url,final_url=response["final_url"],content_type=response["content_type"],captured_at=result["captured_at_utc"],decoded_byte_cap=byte_cap,response_headers=response.get("response_headers",{}))
            records,coverage=parse(parse_source,raw)
            metadata=coverage["native_metadata"]
            if source_id=='govinfo_text':
                expected='/content/pkg/'+params['package']+'/html/'+params['package']+'.htm'
                if urlsplit(response['final_url']).path!=expected: raise ValueError('GovInfo returned a different package path')
                result['source_binding']={'package_id':params['package'],'collection':params['package'].split('-')[0],
                    'basis':'Exact requested GovInfo public rendition path; legal stage and relevance remain unverified'}
            if source_id in {'congress_bill','congress_actions','congress_texts'}:
                from .congress import bind_identity
                result['source_binding']=bind_identity(source_id,params,metadata)
            if source_id.startswith("sec_"):
                if str(int(metadata["cik"]))!=str(int(params["cik"])): raise ValueError("Returned issuer CIK does not match the requested issuer")
                if source_id=="sec_concept" and any(metadata[k]!=params[k] for k in ("taxonomy","tag")): raise ValueError("Returned XBRL concept does not match the request")
            if source.get("identity_field"):
                if str(metadata[source["identity_field"]])!=str(params[source["identity_parameter"]]): raise ValueError("Returned detail identity does not match the request")
            if source_id=="uk_legislation_xml" and metadata.get("document_uri"):
                returned_path=urlsplit(metadata["document_uri"]).path.strip("/").split("/")[:3]
                if returned_path!=[str(params[k]) for k in ("type","year","number")]: raise ValueError("Returned statute identity does not match the request")
            result.update(records=records,coverage=coverage,status="usable" if records else "empty")
            result["quality"].update(structure_valid=True,record_count=len(records),usable_observation_count=sum(x.get("value") is not None for x in records) if source_id=="worldbank" else None)
            if source["format"]=="document":
                result["quality"]["body_diagnostics"]=coverage["native_metadata"].get("body_diagnostics",{})
                result["quality"]["readable_document"]=True
                if any(r.get("metadata",{}).get("table_extraction")=="unavailable" for r in records):
                    result["limitations"].append("PDF table extraction unavailable; page text can mix columns and adjacent records. Inspect original PDF before numerical use.")
                if source.get("profile"):
                    p=source["profile"]
                    result["source_binding"]={"profile_id":source["profile_id"],"entity":p["entity"],"jurisdiction":p.get("jurisdiction"),"cik":p.get("cik"),"basis":"Curated issuer/authority host; target period, metric and outcome remain unverified"}
                if source.get('congress_text_binding'): result['source_binding']=source['congress_text_binding']
                if source.get('discovery_binding'): result['source_binding']=source['discovery_binding']
            if not records: result["limitations"].append("Valid zero-result response; not proof of event absence")
            if coverage["more_available"]: result["limitations"].append("Additional records exist; this is a bounded result window")
        except Exception as exc:
            # Avoid copying arbitrary exception text, which may contain credentials.
            result["error"]={"type":type(exc).__name__,"message":str(exc) if isinstance(exc,(ValueError,json.JSONDecodeError,ET.ParseError)) else "Acquisition failed; inspect HTTP metadata and local transport logs"}
            result["quality"]["structure_valid"]=False
            if isinstance(exc,DocumentRejected): result["quality"]["body_diagnostics"]=exc.diagnostics
        return self._finish_capture(result,folder,attempt)

    def _finish_capture(self,result,folder,attempt):
        result["completed_at_utc"]=now()
        result["budget"]=self.budget()
        capture_path=folder/"capture.json"
        capture_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        with self.db:
            self.db.execute("UPDATE attempts SET status=?,capture=? WHERE id=?",(result["status"],str(capture_path.relative_to(self.root)),attempt))
        return result

    def call(self, tool, arguments):
        """Dispatch ordinary JSON function calls from any agent framework."""
        from .contracts import validate_call
        validate_call(tool,arguments,TOOL_DEFINITIONS)
        if tool=='intelligence_discover': return self.discover(**arguments)
        if tool=='intelligence_acquire_link': return self.acquire_link(**arguments)
        if tool in {"intelligence_outline", "intelligence_search", "intelligence_part"}:
            from .navigation import navigate
            args=dict(arguments)
            capture,raw=self._saved(args.pop("capture_id"))
            return navigate(capture,raw,tool.removeprefix("intelligence_"),**args)
        if tool=="intelligence_catalog": return catalog(arguments.get("domain"))
        if tool=="intelligence_fetch": return self.fetch(arguments["source_id"],arguments.get("parameters"))
        if tool=="intelligence_read": return self.read(arguments["url"])
        if tool=="intelligence_profile": return self.profile(arguments["profile_id"],arguments.get("url"))
        if tool=="intelligence_links": return self.links(arguments["capture_id"],arguments.get("limit",40))
        if tool=="intelligence_tables": return self.tables(arguments["capture_id"],arguments.get("max_rows",200))
        if tool=="intelligence_provisions": return self.provisions(arguments["capture_id"],arguments.get("provision_id"),arguments.get("max_chars",30000))
        if tool=='intelligence_bill_text': return self.bill_text(arguments['capture_id'],arguments['version_index'],arguments.get('format_index',0),detail_capture_id=arguments.get('detail_capture_id'))
        if tool=="intelligence_budget": return self.budget()
        raise ValueError("Unknown toolbox function")


TOOL_DEFINITIONS=[
    {"type":"function","function":{"name":"intelligence_bill_text","description":"Read one official original text selected from a hash-verified Congress text index. Use zero-based version and format indices. Public/private law formats require detail_capture_id with matching official law number. Preserves version and parent captures; counts one request, with no automatic redirects. Draft/enrolled text is not proof of enactment.","parameters":{"type":"object","properties":{"capture_id":{"type":"string"},"version_index":{"type":"integer","minimum":0},"format_index":{"type":"integer","minimum":0},"detail_capture_id":{"type":"string"}},"required":["capture_id","version_index"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"intelligence_catalog","description":"List available source contracts and parameter guidance. Use before fetch. Key onboarding entries are not executable.","parameters":{"type":"object","properties":{"domain":{"type":"string","enum":["finance","politics","health","environment","science","news"]}},"additionalProperties":False}}},
    {"type":"function","function":{"name":"intelligence_fetch","description":"Fetch one bounded source data, news lead, public file, or metadata response. Raw capture, native records, limitations and budget are returned. A usable status does not verify relevance or truth. No automatic retries or pagination.","parameters":{"type":"object","properties":{"source_id":{"type":"string","enum":list(SOURCES)},"parameters":{"type":"object","description":"Only keys declared by this source in intelligence_catalog."}},"required":["source_id"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"intelligence_budget","description":"Inspect the persistent HTTP attempt budget, including failed and interrupted attempts.","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
    {"type":"function","function":{"name":"intelligence_read","description":"Read a saved discovery URL on an explicitly allowed host. Reuses HTML, PDF, JSON and CSV readers; retains raw data and parse failures. Inspect document diagnostics. No browser fallback or OCR is automatic.","parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"intelligence_profile","description":"Read a curated issuer or election-authority page. Catalog lists profile IDs and hosts. Optional detail URL must belong to that profile. Source identity does not prove event stage, target metric or outcome.","parameters":{"type":"object","properties":{"profile_id":{"type":"string"},"url":{"type":"string"}},"required":["profile_id"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"intelligence_links","description":"Inspect links and table-row context from a saved HTML capture without any HTTP request. Select exact period, document type and authority before reading detail URLs.","parameters":{"type":"object","properties":{"capture_id":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}},"required":["capture_id"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"intelligence_tables","description":"Read HTML table cells from a hash-verified saved body without new requests. Retains row/column spans and surrounding unit context. No metric, unit, fiscal period or GAAP inference.","parameters":{"type":"object","properties":{"capture_id":{"type":"string"},"max_rows":{"type":"integer","minimum":1,"maximum":1000}},"required":["capture_id"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"intelligence_provisions","description":"Read an exact clause or schedule from saved UK legislation XML without new requests, including provisions beyond the initial excerpt limit. Omit provision_id to inspect the bounded provision index. Text truncation and not-found status are explicit.","parameters":{"type":"object","properties":{"capture_id":{"type":"string"},"provision_id":{"type":"string"},"max_chars":{"type":"integer","minimum":500,"maximum":100000}},"required":["capture_id"],"additionalProperties":False}}},
]

from .navigation import definitions as navigation_definitions
TOOL_DEFINITIONS.extend(navigation_definitions())
from .discovery import definitions as discovery_definitions
TOOL_DEFINITIONS.extend(discovery_definitions())
