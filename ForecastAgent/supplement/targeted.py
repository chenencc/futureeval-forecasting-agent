"""Timestamped targeted material recovery using remaining parent allowances.

This executor never opens a model, rewrites a parent or submits a forecast.
Search snippets are leads. Page readability does not certify relevance.
"""
import argparse
import base64
import copy
import hashlib
import json
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from ForecastAgent.supplement.stage import save, now, fetch_document
from ForecastAgent.readers.browser import render_page
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.providers.tavily_search import search_batch, canonical_url
from ForecastAgent.providers.tavily_extract import extract_basic
from ForecastAgent.providers.exa_search import search as exa_search
from ForecastAgent.evidence.acquisition_quality import discovery_score
from ForecastAgent.runtime.task_lock import task_lock

CAPS={'tavily_basic':3,'exa':1,'initial_http':8,'extract':1,
      'supplement_http':2,'browser':2}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def eligible(url):
    p=urlsplit(url);host=(p.hostname or '').lower()
    if not (p.scheme in {'http','https'} and host and not p.username and not p.password
            and host not in {'localhost','metaculus.com'} and not host.endswith(
                ('.localhost','.local','.internal','.metaculus.com'))):return False
    try:return ipaddress.ip_address(host).is_global
    except ValueError:return True  # HTTP/browser tools also validate resolved addresses.


def parent_counts(bundle, supplement, prior_extract=0):
    attempts=supplement.get('attempts',[])
    counts={'tavily_basic':len(bundle.get('searches',[])),
            'exa':len(bundle.get('exa_searches',[])),
            'initial_http':len(bundle.get('fetch_attempts',[])),
            'extract':len(bundle.get('extract_attempts',[]))+prior_extract,
            'supplement_http':sum(a['method']=='http' for a in attempts),
            'browser':sum(a['method']=='browser' for a in attempts)}
    if any(v>CAPS[k] for k,v in counts.items()):
        raise ValueError('Parent already exceeds a frozen allowance')
    return counts


class Ledger:
    def __init__(self, path, identity):
        self.path=Path(path)
        if self.path.exists():
            self.data=json.loads(self.path.read_text(encoding='utf-8'))
            if self.data['identity']!=identity:
                raise ValueError('Parent, inputs or allowance changed; refuse restart')
        else:
            self.data={'schema':'targeted-gap-supplement-v1','identity':identity,
                       'started_at_utc':now(),'attempts':[],'captures':{},'finished':False}
            self.flush()

    def flush(self): save(self.path,self.data)

    def remaining(self,kind):
        return CAPS[kind]-self.data['identity']['parent_counts'][kind]-sum(
            a['kind']==kind for a in self.data['attempts'])

    def reserve(self,kind,signature,request):
        # Reserved, failed and unknown outcomes all consume the same allowance.
        if self.data['finished'] or self.remaining(kind)<=0 or any(
                a['kind']==kind and a['signature']==signature for a in self.data['attempts']):
            return None
        attempt={'kind':kind,'signature':signature,'request':request,
                 'status':'reserved','started_at_utc':now()}
        self.data['attempts'].append(attempt);self.flush()
        return attempt

    def call(self,kind,signature,request,operation):
        attempt=self.reserve(kind,signature,request)
        if attempt is None:return None
        try:
            response=operation()
            attempt.update(status='completed',response=response)
            return response
        except Exception as exc:
            attempt.update(status='failed',error_type=type(exc).__name__)
            code=getattr(exc,'http_status',getattr(exc,'code',None))
            if isinstance(code,int):attempt['http_status']=code
            if getattr(exc,'audit',None):attempt['transport_audit']=exc.audit
            return None
        finally:
            attempt['finished_at_utc']=now();self.flush()


def store(ledger,folder,url,page,origin):
    if not eligible(url):return
    diagnostic=body_diagnostics(page.get('content',''),documents=page.get('documents',[]))
    page={**page,'body_diagnostics':diagnostic,'targeted_supplement_provenance':{
        'parent_bundle_sha256':ledger.data['identity']['parent_bundle_sha256'],
        'source_origin':origin,'relevance_verified':False}}
    key=hashlib.sha256((origin+url).encode()).hexdigest()
    path=Path(folder)/'captures'/f'{key}.json';save(path,page)
    ledger.data['captures'][canonical_url(url)]={'file':path.relative_to(folder).as_posix(),
        'json_sha256':sha(path),'url':url,'usable_text':diagnostic['usable_text'],
        'retrieved_at_utc':page.get('retrieved_at_utc'),'origin':origin,
        'chars':len(page.get('content',''))}
    ledger.flush()


def readable(ledger):
    return sum(c['usable_text'] for c in ledger.data['captures'].values())


def extract(ledger,folder,urls,key):
    urls=list(dict.fromkeys(u for u in urls if eligible(u)))[:5]
    if not urls or not key:return
    data=ledger.call('extract','one-basic-batch',{'urls':urls,'extract_depth':'basic'},
                     lambda:extract_basic(urls,key))
    for row in (data or {}).get('results',[]):
        u=row.get('url','');content=row.get('raw_content','')
        if canonical_url(u) not in {canonical_url(x) for x in urls} or not isinstance(content,str):continue
        raw=content.encode('utf-8')
        store(ledger,folder,u,{'url':u,'content':content,'retrieved_at_utc':now(),
            'sha256':hashlib.sha256(raw).hexdigest(),'raw_response_base64':base64.b64encode(raw).decode(),
            'capture_method':'tavily_basic_extract','raw_payload_kind':'vendor_extracted_markdown_not_original_http_body'},
            'remaining_basic_extract')


def candidates(bundle,ledger,config):
    rows=[]
    for batch in bundle.get('searches',[])+bundle.get('exa_searches',[]):
        rows.extend(batch.get('results',[]))
    rows.extend(bundle.get('source_leads',{}).values())
    for a in ledger.data['attempts']:
        if a['kind'] in {'tavily_basic','exa'}:
            rows.extend((a.get('response') or {}).get('results',[]))
    dedup={}
    for row in rows:
        u=row.get('url','')
        if not eligible(u):continue
        host=(urlsplit(u).hostname or '').lower()
        official=any(host==d or host.endswith('.'+d) for d in config.get('preferred_domains',[]))
        score=discovery_score(bundle['request'],u,row.get('title',''),official,row.get('published_date'))
        value={**row,'url':u,'priority':score,'preferred_domain_hint':official,'relevance_verified':False}
        key=canonical_url(u)
        if key not in dedup or score>dedup[key]['priority']:dedup[key]=value
    return sorted(dedup.values(),key=lambda x:(-x['priority'],x['url']))


def run_one(config,output):
    parent=Path(config['parent_collection']);raw=json.loads((parent/'bundle.json').read_text(encoding='utf-8'))
    ident=raw['request']['id']
    if str(ident)!=str(config['id']):raise ValueError('Wrong parent question')
    sp=Path(config['parent_supplement']) if config.get('parent_supplement') else None
    old_supp=json.loads(sp.read_text(encoding='utf-8')) if sp and sp.exists() else {}
    prior=Path(config['prior_repair']) if config.get('prior_repair') else None
    prior_bundle=json.loads((prior/'collection/bundle.json').read_text(encoding='utf-8')) if prior else None
    prior_count=(len(prior_bundle.get('extract_attempts',[]))-len(raw.get('extract_attempts',[]))) if prior_bundle else 0
    inherited=parent_counts(raw,old_supp,prior_count)
    frozen={'parent_files_sha256':{str(p):sha(p) for p in parent.rglob('*') if p.is_file()},
            'parent_bundle_sha256':sha(parent/'bundle.json'),'parent_counts':inherited,
            'executor_source_sha256':sha(__file__),
            'source_release':'v1.0.4','executor_release_base':'v1.0.5',
            'config':config,'budget_reset':False,'models':0,'analysis':0,'submissions':0,
            'scope':'One timestamped child supplement; existing source ledgers are immutable.'}
    if sp and sp.exists():frozen['parent_supplement_sha256']=sha(sp)
    if prior:frozen['prior_repair_bundle_sha256']=sha(prior/'collection/bundle.json')
    folder=Path(output)/str(ident)
    with task_lock(folder):
        ledger=Ledger(folder/'ledger.json',frozen)
        if not ledger.data['finished']:
            if prior_bundle:
                # Reuse the previous credit and timestamp, never call Extract twice.
                report=json.loads((prior/'report.json').read_text(encoding='utf-8'))
                if not report.get('original_files_unchanged') or not report.get('resume_without_new_calls'):
                    raise ValueError('Prior repair provenance unavailable')
                migration=json.loads((prior/'migration.json').read_text(encoding='utf-8'))
                if migration['parent_files_sha256']!={p.relative_to(parent).as_posix():sha(p) for p in parent.rglob('*') if p.is_file()}:
                    raise ValueError('Prior repair is not bound to this parent')
                for u,page in prior_bundle.get('pages',{}).items():store(ledger,folder,u,page,'previous_v105_probe_reused')
            failed=[a['url'] for a in raw.get('fetch_attempts',[]) if a.get('status')=='failed' and a.get('url')]
            if not readable(ledger) and failed and ledger.remaining('extract')>0:
                extract(ledger,folder,failed,os.environ.get('TAVILY_API_KEY'))
            if not readable(ledger):
                if ledger.remaining('tavily_basic')>0:
                    ledger.call('tavily_basic',config['query'],{'query':config['query'],'topic':config.get('topic','general'),'depth':'basic','max_results':10},
                        lambda:search_batch(config['query'],os.environ['TAVILY_API_KEY'],topic=config.get('topic','general')))
                if ledger.remaining('exa')>0:
                    ledger.call('exa',config['query'],{'query':config['query'],'type':'auto','numResults':10,'contents':False},
                        lambda:exa_search(config['query'],os.environ['EXA_API_KEY']))
                selected=candidates(raw,ledger,config)
                old_attempted={canonical_url(a.get('url','')) for a in raw.get('fetch_attempts',[])+old_supp.get('attempts',[])}
                old_attempted.update(a['signature'] for a in ledger.data['attempts'] if a['kind'] in {'initial_http','supplement_http'})
                failed_now=[];reads=0
                for row in selected:
                    u=row['url'];canonical=canonical_url(u)
                    if canonical in old_attempted or ledger.data['captures'].get(canonical,{}).get('usable_text'):continue
                    kind='initial_http' if ledger.remaining('initial_http')>0 else 'supplement_http'
                    if ledger.remaining(kind)<=0:break
                    page=ledger.call(kind,canonical,{'url':u,'source_selection':row},lambda:fetch_document(u))
                    reads+=1
                    if page:store(ledger,folder,u,page,'observed_search_free_capture')
                    if not page or not (page.get('body_diagnostics') or {}).get('usable_text'):failed_now.append(u)
                    if readable(ledger)>=3 or reads>=4:break
                if failed_now and ledger.remaining('extract')>0:
                    extract(ledger,folder,failed_now,os.environ.get('TAVILY_API_KEY'))
                if not readable(ledger):
                    for row in selected[:2]:
                        u=row['url']
                        page=ledger.call('browser',canonical_url(u),{'url':u,'request_limit':25},
                                        lambda:render_page(u,retrieved_at=now()))
                        if page:store(ledger,folder,u,page,'bounded_browser_render')
                        if readable(ledger):break
            ledger.data.update(finished=True,finished_at_utc=now())
            ledger.flush()
        pages={}
        for entry in ledger.data['captures'].values():
            path=folder/entry['file']
            if sha(path)!=entry['json_sha256']:raise ValueError('Supplement capture changed')
            if entry['usable_text']:pages[canonical_url(entry['url'])]=json.loads(path.read_text(encoding='utf-8'))
        if any(sha(Path(p))!=h for p,h in frozen['parent_files_sha256'].items()):raise ValueError('Original snapshot changed')
        totals={k:CAPS[k]-ledger.remaining(k) for k in CAPS}
        assert all(v<=CAPS[k] for k,v in totals.items())
        package={'schema':'timestamped-targeted-supplement-package-v1','question_id':ident,
            'parent_request':copy.deepcopy(raw['request']),'parent_bundle_sha256':frozen['parent_bundle_sha256'],
            'original_snapshot_as_of_utc':raw['request'].get('as_of_utc'),
            'supplement_started_at_utc':ledger.data['started_at_utc'],'supplement_finished_at_utc':ledger.data['finished_at_utc'],
            'pages':pages,'capture_files':ledger.data['captures'],
            'missing_official_rules_preserved':True,'original_results_unchanged':True,
            'relevance_verified':False,'analysis_run':False,'forecast_submitted':False,
            'budget_totals_including_parent':totals,'budget_reset':False,
            'limitations':['Current repair captures are not evidence that existed in the original snapshot.',
                'A readable page may concern a different period or only background.',
                'Official missing rules remain unresolved; no future outcome is inferred.']}
        save(folder/'supplement-package.json',package)
        return {'id':ident,'title':raw['request']['question'],'usable_bodies':len(pages),
                'state':'material_available_with_gaps' if pages else 'no_readable_material',
                'new_attempts':{k:sum(a['kind']==k for a in ledger.data['attempts']) for k in CAPS},
                'budget_totals':totals,'reused_captures':sum(e['origin']=='previous_v105_probe_reused' for e in ledger.data['captures'].values()),
                'models':0,'analysis_calls':0,'submissions':0,'package':str(folder/'supplement-package.json')}


def run(plan,root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    identity=root/'plan.json'
    if identity.exists() and json.loads(identity.read_text())!=plan:raise ValueError('Frozen recovery plan changed')
    save(identity,plan)
    summaries=[]
    for task in plan['tasks']:
        result=run_one(task,root/'tasks');summaries.append(result)
        save(root/'summary.json',{'schema':'targeted-gap-supplement-summary-v1','tasks':summaries,
             'originals_unchanged':True,'budgets_reset':False,'models':0,'analysis_calls':0,'submissions':0})
        print(json.dumps(result,ensure_ascii=True),flush=True)
    return summaries


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    if not args.execute:p.error('Explicit --execute is required for bounded network recovery')
    run(json.loads(args.plan.read_text(encoding='utf-8')),args.root)


if __name__=='__main__':main()
