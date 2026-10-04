"""Export local snapshots without retrieval, inference or submission side effects."""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SECRET = re.compile(r'sk-(?:or-v1-)?[A-Za-z0-9_-]{12,}|Bearer\s+\S+|(?i:Authorization\s*[:=]\s*\S+)|(?i:(?:api[_-]?key|access[_-]?token)=)[^&\s]+|(?<![A-Za-z])[A-Za-z]:[\\/][^\s"<>]+')

def clean(value):
    if isinstance(value,str):return SECRET.sub('[REDACTED]',value)
    if isinstance(value,list):return [clean(v) for v in value]
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    return value

def safe_link(url):
    try:
        u=urlsplit(url)
        if u.scheme not in ('https','http') or u.username or u.password:return None
        query=[(k,v) for k,v in parse_qsl(u.query) if not re.search('key|token|secret|signature|auth|sig',k,re.I)]
        return urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),u.fragment))
    except ValueError:return None

def read(path):return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

def task(folder,run_id):
    folder=Path(folder)
    path=folder/'analysis-input.json'
    if not path.exists():raise ValueError('Expected a saved analysis-input.json')
    b=read(path);q=b['request'];ident=str(q.get('id') or q.get('post_id'))
    a=read(folder/'analysis/result.json') or read(folder/'mercury/result.json')
    repair=read(folder/'supplement/state.json') or read(folder/'repair-state.json')
    pages=[]
    for i,(url,p) in enumerate(b.get('pages',{}).items()):
        body=p.get('content','');link=safe_link(url)
        if not link:continue
        pages.append({'id':f'S{i+1:03d}','url':link,'title':p.get('title') or urlsplit(link).hostname,
            'characters':len(body),'body_sha256':hashlib.sha256(body.encode()).hexdigest(),
            'captured_at':p.get('fetched_at') or p.get('captured_at_utc') or p.get('retrieved_at'),
            'excerpt':body[:1800],'excerpt_truncated':len(body)>1800,
            'supplemented':bool(p.get('supplement_provenance')),'truth_verified':False})
    searches=[]
    for channel,key in [('Tavily basic','searches'),('Exa','exa_searches')]:
        for s in b.get(key,[]):
            searches.append({'channel':channel,'query':s.get('query',''),'role':s.get('purpose') or s.get('role') or 'Not recorded',
                'status':'failed' if s.get('error') else 'recorded','at':s.get('attempted_at_utc'),
                'results':[{'url':safe_link(r.get('url','')),'title':r.get('title',''),
                    'snippet':str(r.get('content') or r.get('text') or '')[:400]} for r in s.get('results',[]) if safe_link(r.get('url',''))]})
    fetches=[{'url':safe_link(r.get('url') or r.get('requested_url') or ''),
        'status':r.get('status','unknown'),'error':str(r.get('error') or '')[:250]} for r in b.get('fetch_attempts',[])]
    supplement=[{'tool':r.get('tool'),'url':safe_link(r.get('url') or ''),'status':r.get('status'),
        'error':str(r.get('error') or '')[:250]} for r in repair.get('attempts',[])]
    forecast=a.get('forecast') or {}
    p=a.get('clipped_probability_yes',a.get('probability_yes'))
    kind=a.get('type') or ('binary' if p is not None else q.get('type','unknown'))
    forecast={k:v for k,v in forecast.items() if k in ('probability_yes','probabilities','option_probabilities','bin_probabilities','cdf_knots','quantiles','median','prediction')}
    if p is not None:forecast['probability_yes']=p
    gaps=a.get('remaining_diagnostic_gaps',[]) + b.get('gaps',[])
    stage=lambda id,title,status,count:{'id':id,'title':title,'status':status,'count':count}
    stages=[stage('rules','Question & rules','recorded',1),stage('plan','Acquisition plan','recorded' if b.get('plan') else 'not_recorded',len(b.get('plan',[]))),
        stage('search','Search & discovery','recorded' if searches else 'not_recorded',len(searches)),
        stage('read','Read original sources','recorded' if pages else 'not_recorded',len(pages)),
        stage('supplement','Independent supplement','recorded' if supplement or b.get('supplement_lineage') else 'not_recorded',len(supplement)),
        stage('analysis','Mercury analysis & rereading','completed' if a.get('status')=='completed' else 'not_recorded',2 if a.get('routing',{}).get('second_call_required') or a.get('second_call_required') else 1 if a else 0),
        stage('forecast','Validated forecast','recorded' if forecast else 'not_recorded',1 if forecast else 0),
        stage('delivery','Submission receipt','not_recorded',0)]
    return clean({'id':ident,'title':q['question'],'type':kind,'created_at':b.get('created_at'),
        'rules':q.get('resolution_criteria',''),'fine_print':q.get('fine_print',''),
        'run_id':str(run_id),'run_url':f'https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/{run_id}',
        'question_url':f'https://www.metaculus.com/questions/{ident}/','input_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'record_scope':'Historical archived trial; source commit not verified as v1.0.1.',
        'status':'completed_with_gaps' if gaps else 'completed' if forecast else 'snapshot_only',
        'stages':stages,'plan':[{k:n.get(k) for k in ('id','condition','priority','query')} for n in b.get('plan',[])],
        'searches':searches,'sources':pages,'fetches':fetches,'supplement':supplement,'forecast':forecast,
        'analysis':{'status':a.get('status','not_recorded'),'model':'inception/mercury-decide:free',
            'routing':a.get('routing',{}),'first_probability_yes':a.get('first_probability_yes'),'gaps':gaps},
        'usage':{'acquisition_http_attempts':len(b.get('model_attempts',[])),'search_attempts':len(searches),
            'fetch_attempts':len(fetches),'analysis_http_attempts':None,'tokens':None},
        'delivery':{'status':'not_recorded','note':'No submission receipt is present in this exported archive.'},
        'warning':'Historical questions with current-information snapshots. Not a leakage-free forecasting backtest.'})

def export(spec,output):
    config=read(Path(spec));rows=[task(t['path'],t['run_id']) for t in config['tasks']]
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('Duplicate task IDs')
    data={'schema_version':1,'release':'1.0.1','release_commit':'c9bfab44a670f4307ebc2330866479c82aec71ef',
        'generated_at':datetime.now(timezone.utc).isoformat(),'mode':'archived_preview',
        'notice':'Release 1.0.1 presentation contract. Archived trial records are not live competition status.', 'tasks':rows}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Exported {len(rows)} tasks without provider calls')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--spec',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();export(args.spec,args.output)
