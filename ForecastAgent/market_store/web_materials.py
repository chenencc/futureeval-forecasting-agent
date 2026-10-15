"""Build immutable display packets from hash-verified local native artifacts.

No Web code, provider calls, search, inference, archive extraction or native task
mutation is used. Missing local material remains a presentation import gap.
"""
import hashlib
import json
from pathlib import Path
import re
import tarfile
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

from .store import clock,digest,encoded

ARMS=('original','mapped','official_map')
SECRET=re.compile(r'sk-(?:or-v1-)?[A-Za-z0-9_-]{12,}|Bearer\s+\S+|(?i:Authorization\s*[:=]\s*\S+)|(?i:(?:api[_-]?key|access[_-]?token)=)[^&\s]+|(?<![A-Za-z])[A-Za-z]:[\\/][^\s"<>]+')
PRIVATE=re.compile(r'^(?:authorization|headers|api[_-]?key|secret|credential|raw_prompt|system_prompt|raw_response|(?:local|storage|filesystem)_path)$',re.I)


def safe_link(value):
    if not isinstance(value,str): return None
    try:
        u=urlsplit(value)
        if u.scheme not in ('https','http') or u.username or u.password or not u.hostname: return None
        query=[(k,v) for k,v in parse_qsl(u.query) if not re.search('key|token|secret|signature|auth|sig',k,re.I)]
        return urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),u.fragment))
    except ValueError: return None


def clean(value):
    if isinstance(value,str): return SECRET.sub('[REDACTED]',value)
    if isinstance(value,list): return [clean(x) for x in value]
    if isinstance(value,dict):
        return {k:(safe_link(v) if k in {'url','href','final_url','requested_url'} else clean(v))
                for k,v in value.items() if not PRIVATE.fullmatch(k)}
    return value


def install(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS web_documents(display_sha256 TEXT PRIMARY KEY,
      text TEXT NOT NULL,byte_length INTEGER NOT NULL,first_imported_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS web_packets(id TEXT PRIMARY KEY,
      market_key TEXT NOT NULL REFERENCES markets(key),batch_id TEXT NOT NULL,
      source_kind TEXT NOT NULL,forecast_fingerprint TEXT NOT NULL,
      material_status TEXT NOT NULL,material_sha256 TEXT,observed_at TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS web_packet_binding ON web_packets(market_key,batch_id,forecast_fingerprint,observed_at);
    CREATE VIEW IF NOT EXISTS latest_web_packets AS SELECT * FROM web_packets p WHERE p.rowid=(
      SELECT x.rowid FROM web_packets x WHERE x.market_key=p.market_key AND x.batch_id=p.batch_id
      AND x.forecast_fingerprint=p.forecast_fingerprint ORDER BY x.observed_at DESC,x.rowid DESC LIMIT 1);
    ''')
    db.execute("INSERT OR REPLACE INTO metadata VALUES('web_contract','market-web-database-v1')")
    db.commit()


def fingerprint(forecast):
    """Bind display material to exact route outputs and actual recorded clocks."""
    fields=('status','eventual_probability_yes','price_means','response_sha256','state_sha256','sealed_at')
    return digest({'anchor_at':forecast.get('anchor_at'),'sealed_at':forecast.get('sealed_at'),
        'arms':{arm:{k:value.get(k) for k in fields} for arm,value in forecast.get('arms',{}).items()}})


class Objects:
    def __init__(self,root,extra=(),data_root=None):
        self.root=Path(root)
        self.extra=[Path(x).resolve() for x in extra]
        self.generic=(Path(data_root) if data_root else self.root.parent.parent)/'blobs/sha256'
        self.pack_checked=set()

    def load(self,sha):
        if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha): raise ValueError('Invalid object hash')
        locations=[self.root/'objects'/sha[:2]/sha,self.generic/sha[:2]/sha]
        locations += [p for folder in self.extra for p in (folder/(sha+'.json'),folder/sha,folder/sha[:2]/sha)]
        for path in locations:
            if not path.is_file(): continue
            if path.stat().st_size>64*1024**2: raise ValueError('Native object exceeds local reading bound')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=sha: raise ValueError('Native object byte hash differs')
            target=self.root/'objects'/sha[:2]/sha
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(raw)
            return json.loads(raw)
        raise FileNotFoundError('Hash-addressed native object is not synchronized')

    def prepare_pack(self,db,key):
        """Read bounded verified local task packs; never extract filesystem paths."""
        if key in self.pack_checked: return
        self.pack_checked.add(key)
        refs=list(db.execute('SELECT path,sha256 FROM material_refs WHERE market_key=?',(key,)))
        wanted={r['sha256'] for r in refs if any(r['path'].endswith(suffix) for suffix in
            ('/analysis-input.json','/analysis-integrity.json','/material-integrity.json','/state.json',
             '/result.json','/decision/request.json','/decision/identity.json','/decision/response.json'))}
        locations=list(db.execute('SELECT sha256,data FROM pack_locations WHERE market_key=? ORDER BY observed_at DESC,rowid DESC',(key,)))
        seen=set()
        for pack in locations:
            sha=pack['sha256']
            if sha in seen: continue
            seen.add(sha)
            if len(seen)>20: break
            candidates=[self.generic/sha[:2]/sha,self.root/'objects'/sha[:2]/sha]
            candidates += [folder/(sha+'.tar.xz') for folder in self.extra]
            path=next((p for p in candidates if p.is_file()),None)
            if path is None: continue
            if path.stat().st_size>128*1024**2: raise ValueError('Local task pack exceeds reading bound')
            hashed=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024**2),b''): hashed.update(block)
            if hashed.hexdigest()!=sha: raise ValueError('Local task pack hash differs')
            expanded=0;names=set()
            with tarfile.open(path,'r|xz') as archive:
                for member in archive:
                    match=re.fullmatch('objects/([0-9a-f]{64})',member.name)
                    if not match or not member.isfile() or member.name in names:
                        raise ValueError('Unsafe or duplicate local pack member')
                    names.add(member.name);expanded+=member.size
                    if expanded>1024**3 or len(names)>20000: raise ValueError('Local pack expansion exceeds bound')
                    if match[1] not in wanted: continue
                    if member.size>64*1024**2: raise ValueError('Native JSON object exceeds bound')
                    raw=archive.extractfile(member).read(64*1024**2+1)
                    if len(raw)!=member.size or hashlib.sha256(raw).hexdigest()!=match[1]:
                        raise ValueError('Local pack object integrity differs')
                    target=self.root/'objects'/match[1][:2]/match[1]
                    if not target.exists():
                        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)


def question(request,market):
    if (request.get('market_research_key')!=market['key'] or request.get('question')!=market['question'] or
        request.get('resolution_criteria')!=market['rules']):
        raise ValueError('Material question or frozen rules differ')


def validate_decision(state,forecast,result,request=None,identity=None,response=None):
    expected=forecast.get('state_sha256')
    if expected:
        if digest(state)!=expected or result.get('state_sha256')!=expected:
            raise ValueError('Saved decision state hash differs')
        mode='catalog_state_hash'
    else:
        if not request or not identity or not response or request.get('state')!=state:
            raise ValueError('Legacy saved decision request is unavailable or differs')
        sha=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if identity.get('request_sha256')!=sha or digest(response)!=forecast.get('response_sha256'):
            raise ValueError('Legacy saved decision request/response binding differs')
        mode='legacy_request_response_chain'
    for k in ('eventual_probability_yes','price_means','response_sha256','sealed_at'):
        if k in forecast and result.get(k)!=forecast[k]: raise ValueError('Saved decision result differs')
    return {'state_sha256':digest(state),'binding_mode':mode}


def packet(bundle,market,states,route_status,proof,documents):
    question(bundle['request'],market)
    pages=bundle.get('pages',{})
    bodies={}
    sources=[]
    for url,page in pages.items():
        text=page.get('content')
        if not isinstance(text,str) or not text: continue
        sha=hashlib.sha256(text.encode()).hexdigest()
        if page.get('content_sha256') and page['content_sha256']!=sha:
            raise ValueError('Saved page content hash differs')
        bodies[(url,sha)]=text
        if not safe_link(url) or page.get('body_diagnostics',{}).get('usable_text') is False: continue
        if len(text)<400 and re.search(r'verifying your browser|incident id|access denied|enable javascript',text,re.I): continue
        display=clean(text)
        display_sha=hashlib.sha256(display.encode()).hexdigest()
        documents[display_sha]=display
        sources.append({'id':'S'+str(len(sources)+1).zfill(3),'url':safe_link(url),
            'title':clean(page.get('title') or urlsplit(url).hostname),'characters':len(text),
            'body_sha256':sha,'captured_at':page.get('retrieved_at_utc') or page.get('fetched_at') or
                page.get('captured_at_utc') or page.get('retrieved_at'),
            'excerpt':display[:1800],'excerpt_truncated':len(display)>1800,
            'supplemented':bool(page.get('supplement_provenance') or page.get('web_supplement_origin') or page.get('targeted_supplement_provenance')),
            'body_ref':'data/bodies/'+display_sha+'.txt','display_sha256':display_sha,'body_redacted':display!=text})
    evidence={arm:[] for arm in ARMS}
    scoring={arm:[] for arm in ARMS}
    graphs={}
    binding_gaps=[]
    for arm,state in states.items():
        try:
            question(state['question'],market)
            by_id={}
            for source in state.get('sources',[]):
                sid=source['source_id']
                if sid in by_id: raise ValueError('Duplicate scoring source ID')
                pair=(source.get('url'),source.get('body_sha256'))
                if pair not in bodies: raise ValueError('Scoring source URL and body hash do not bind')
                by_id[sid]=bodies[pair]
            for item in state.get('evidence',[]):
                body=by_id.get(item.get('source_id'))
                start,end=item.get('start'),item.get('end')
                valid=(body is not None and type(start) is int and type(end) is int and
                       0<=start<=end<=len(body) and body[start:end]==item.get('text'))
                if not valid:
                    binding_gaps.append({'code':'saved_excerpt_span_not_verified','arm':arm,
                        'evidence_id':item.get('evidence_id'),'source_id':item.get('source_id')})
            evidence[arm]=clean(state.get('evidence',[]))
            scoring[arm]=clean(state.get('sources',[]))
            graph=state.get('research_map') or {}
            if isinstance(graph,dict) and isinstance(graph.get('graph'),dict): graph=graph['graph']
            if graph: graphs[arm]=clean(graph)
        except (ValueError,KeyError,TypeError) as exc:
            route_status[arm]={'status':'integrity_failed','reason':str(exc)}
    searches=[]
    for channel,key in (('Tavily','searches'),('Exa','exa_searches')):
        for item in bundle.get(key,[]):
            searches.append(clean({'channel':channel,'query':item.get('query',''),'status':item.get('status','recorded'),
                'role':item.get('role') or item.get('purpose'),'at':item.get('attempted_at_utc'),
                'results':[{'url':safe_link(r.get('url')),'title':r.get('title',''),
                    'snippet':str(r.get('content') or r.get('text') or '')[:400]} for r in item.get('results',[])]}))
    fetched=[clean({'url':safe_link(r.get('url') or r.get('requested_url')),'status':r.get('status','unknown'),
        'error':str(r.get('error') or '')[:250]}) for r in bundle.get('fetch_attempts',[])]
    captures=[s['captured_at'] for s in sources if s.get('captured_at')]
    selected=graphs.get('mapped') or {}
    return {'sources':sources,'searches':searches,'fetches':fetched,
        'gaps':clean(list(bundle.get('gaps') or [])+list(bundle.get('capture_gaps') or [])+binding_gaps),
        'plan':clean(bundle.get('plan') or []),'graph':selected,'graphs':graphs,
        'map_status':selected.get('status','saved_map_not_available'),
        'evidence':[],'scoring_sources':[], # Legacy shared-route fallback is intentionally disabled.
        'evidence_by_arm':evidence,'sources_by_arm':scoring,'route_statuses':route_status,
        'frozen_at':bundle.get('market_capture',{}).get('frozen_at'),
        'latest_capture_at':max(captures,default=None),'material_sha256':digest(bundle),
        'graph_sha256':digest(selected) if selected else None,'provenance':clean(proof)}


def native_packet(db,objects,task,documents):
    key=task['id']; market=task['market']; forecast=task.get('forecast') or {}
    prefix='tasks/'+digest(key)+'/'
    stage=task.get('analysis_stage') or 'analysis'
    def get(name,optional=False):
        rows=list(db.execute('SELECT sha256 FROM material_refs WHERE market_key=? AND path=? ORDER BY rowid DESC',(key,prefix+name)))
        for retry in range(2):
            for row in rows:
                try: return objects.load(row[0])
                except FileNotFoundError: continue
            if retry==0 and rows: objects.prepare_pack(db,key)
        if optional: return {}
        raise FileNotFoundError('Required native material is not synchronized: '+name)
    base,marker=('evidence-capture-v2/analysis-input.json','evidence-capture-v2/analysis-integrity.json') if stage=='analysis-evidence-v2' else (
        ('body-recovery-v1/analysis-input.json','body-recovery-v1/analysis-integrity.json') if stage=='analysis-recovered-v1' else ('analysis-input.json','material-integrity.json'))
    bundle=get(base)
    if digest(bundle)!=get(marker)['bundle_sha256']: raise ValueError('Native bundle integrity differs')
    states,statuses,proofs={},{},{}
    for arm in ARMS:
        value=forecast.get('arms',{}).get(arm) or {}
        if value.get('status')!='completed':
            statuses[arm]={'status':'not_scored','reason':value.get('status','not_recorded')}
            continue
        try:
            path=stage+'/'+arm+'/'
            state=get(path+'state.json')
            proofs[arm]=validate_decision(state,value,get(path+'result.json'),get(path+'decision/request.json',True),
                get(path+'decision/identity.json',True),get(path+'decision/response.json',True))
            states[arm]=state
            statuses[arm]={'status':'model_input_verified',**proofs[arm]}
        except FileNotFoundError:
            statuses[arm]={'status':'pack_not_synced','reason':'Decision input object not available locally'}
        except (ValueError,KeyError,TypeError) as exc:
            statuses[arm]={'status':'integrity_failed','reason':str(exc)}
    return packet(bundle,market,states,statuses,{'kind':'verified_native_objects','material_path':base,
        'material_sha256':digest(bundle),'forecast_bindings':proofs,'native_archives_modified':False},documents)


def supplement_packet(data_root,row,market,documents):
    value=row['payload']; folder=Path(row['result_file']).resolve().parent
    allowed=Path(data_root).resolve()/'prediction_markets/revisions'
    if not folder.is_relative_to(allowed): raise ValueError('Supplement outside the declared archive')
    def load(name):
        path=folder/name
        if not path.is_file(): raise FileNotFoundError('Supplement material object not synchronized')
        if path.stat().st_size>64*1024**2: raise ValueError('Supplement object too large')
        return json.loads(path.read_bytes())
    result=load('revision-result.json')
    if digest(result)!=row['result_sha256']: raise ValueError('Supplement result hash differs')
    bundle=load('frozen-input.json')
    if digest(bundle)!=row['material_sha256']: raise ValueError('Supplement material hash differs')
    graph_record=load('graph-snapshot.json')
    if digest(graph_record)!=row['graph_sha256']: raise ValueError('Supplement graph record hash differs')
    forecast=value.get('forecast') or {}
    states={}
    statuses={arm:({'status':'pack_not_synced','reason':'Individual saved input not bound'}
        if forecast.get('arms',{}).get(arm,{}).get('status')=='completed' else
        {'status':'not_scored','reason':forecast.get('arms',{}).get(arm,{}).get('status','not_recorded')})
        for arm in ARMS}
    try:
        state=load('mapped-state.json')
        expected=(forecast.get('identity') or {}).get('mapped_state_sha256')
        if not expected or digest(state)!=expected: raise ValueError('Supplement mapped input hash unavailable or differs')
        if forecast.get('arms',{}).get('mapped',{}).get('status')=='completed':
            states['mapped']=state
            statuses['mapped']={'status':'model_input_verified','state_sha256':expected,'binding_mode':'native_revision_identity'}
    except FileNotFoundError: pass
    original_sha=(forecast.get('identity') or {}).get('original_state_sha256')
    if original_sha and 'mapped' in states and forecast.get('arms',{}).get('original',{}).get('status')=='completed':
        # Reuse is allowed only when the recorded original state hash agrees,
        # never because a neighboring route happened to contain useful text.
        for candidate in (states['mapped'],):
            candidate=dict(candidate); candidate.pop('research_map',None)
            if digest(candidate)==original_sha:
                states['original']=candidate
                statuses['original']={'status':'model_input_verified','state_sha256':original_sha,
                    'binding_mode':'native_revision_identity'}
    return packet(bundle,market,states,statuses,{'kind':'verified_local_revision',
        'result_sha256':row['result_sha256'],'graph_record_sha256':row['graph_sha256'],
        'native_archives_modified':False},documents)


def hydrate(db,root,extra_roots=(),data_root=None):
    install(db)
    objects=Objects(root,extra_roots,data_root)
    report={'schema':'market-web-hydration-v1','at':clock(),'packets':[],'new_documents':0}
    tasks=[json.loads(r[0]) for r in db.execute('SELECT data FROM latest_tasks')]
    batches=[(t['id'],'initial_worker','original_worker',t.get('forecast') or {},lambda docs,t=t:native_packet(db,objects,t,docs)) for t in tasks]
    markets={t['id']:t['market'] for t in tasks}
    for r in db.execute('SELECT market_key,data FROM revisions'):
        row=json.loads(r['data']);key=r['market_key']
        if key not in markets: continue
        batches.append((key,row['revision_id'],'material_revision',row['payload'].get('forecast') or {},
            lambda docs,row=row,key=key:supplement_packet(Path(data_root) if data_root else Path(root).parent.parent,row,markets[key],docs)))
    for key,batch,kind,forecast,read in batches:
        docs={}
        try:
            materials=read(docs)
            status='worker_material_available' if materials['sources'] else 'saved_package_no_readable_body'
            error=None
        except FileNotFoundError as exc:
            materials=None;status='pack_not_synced';error=str(exc)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError,tarfile.TarError) as exc:
            materials=None;status='integrity_failed';error=str(exc)
        value={'materials':materials,'import_error':error}
        sha=fingerprint(forecast)
        ident=digest({'key':key,'batch_id':batch,'forecast_fingerprint':sha,'value':value})
        with db:
            for display_sha,text in docs.items():
                before=db.total_changes
                db.execute('INSERT OR IGNORE INTO web_documents VALUES(?,?,?,?)',(display_sha,text,len(text.encode()),clock()))
                report['new_documents']+=db.total_changes-before
            db.execute('INSERT INTO web_packets VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET observed_at=excluded.observed_at',
                (ident,key,batch,kind,sha,status,(materials or {}).get('material_sha256'),clock(),encoded(value).decode()))
        report['packets'].append({'market_key':key,'batch_id':batch,'status':status,
            'route_statuses':(materials or {}).get('route_statuses',{}),'reason':error})
    return report
