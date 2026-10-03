"""Recall-first market pool and a single independent rule-aware ranking call."""
import argparse,hashlib,json,os,re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from ForecastAgent.polymarket_discovery import capture,candidates
from ForecastAgent.polymarket_match import _similarity
TIERS={'same_quantity_same_date','same_quantity_other_cut','driver_or_consequence','weak'}

def short_query(title):
    text=re.sub(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December|20\d{2})\b','',title,flags=re.I)
    tokens=re.findall(r'[A-Za-z][A-Za-z0-9&.-]*',text)
    stop={'will','the','a','an','be','before','after','in','of','to','by','is','its','his','her','for','as','on','and'}
    return ' '.join(t for t in tokens if t.lower() not in stop)[:100]

def pool(payloads,title):
    found={}
    for payload in payloads:
        rows=[(m,'','') for m in payload.get('markets',[]) if isinstance(m,dict)]
        for event in payload.get('events',[]):
            rows.extend((m,event.get('slug',''),event.get('title','')) for m in event.get('markets',[]) if isinstance(m,dict))
        for market,slug,event_title in rows:
            own=market.get('question') or market.get('title')
            if not own:continue
            parsed=candidates({'markets':[market]},own)
            if not parsed:continue
            row=parsed[0];row.update(event_title=event_title,group_item_title=market.get('groupItemTitle'),lexical_score=_similarity(title,own),resolution_source=market.get('resolutionSource'))
            if slug:row['market_url']='https://polymarket.com/event/'+slug
            found.setdefault(row['market_id'],row)
    return list(found.values())[:60]

def parse_ranking(text,size):
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
    value=json.loads(text)
    if not isinstance(value,list):raise ValueError('Ranking must be an array')
    result=[];seen=set()
    for row in value:
        if not isinstance(row,dict):continue
        i=row.get('i')
        if type(i) is not int or not 0<=i<size or i in seen or row.get('tier') not in TIERS:continue
        seen.add(i);result.append({'i':i,'tier':row['tier'],'why':str(row.get('why',''))[:500],'differences':row.get('differences',[])})
        if len(result)==8:break
    if value and not result:raise ValueError('No usable ranking indices')
    return result

def rank(question,rows,path,model):
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    prompt='Rank prediction-market contracts as research evidence for the question. Read event meaning and rules, not keyword similarity. Return ONLY a JSON array of 0..8 objects: {"i":0,"tier":"same_quantity_same_date","why":"deciding clause","differences":["condition mismatch"]}. Tiers: same_quantity_same_date (same event, threshold, dates and resolution conditions); same_quantity_other_cut (same event or measured quantity but different date/threshold); driver_or_consequence (causally informative); weak (related but weak). Reject unrelated events and padding. Participation is not winning; election announcement is not tariffs; meeting is not a phone call or insult. Closing timestamp is not the event deadline. Resolved prices are outcomes, not forecasts. An empty array is valid. Quote deciding clauses and list entity, event-stage, date, threshold and resolution-source differences. Never declare trading equivalence.\nQUESTION '+json.dumps(question,ensure_ascii=False)+'\nCANDIDATES '+json.dumps([{'i':i,'title':r['market_title'],'event':r['event_title'],'rules':str(r.get('resolution_rules') or '')[:2400],'source':r.get('resolution_source'),'end_time':r.get('market_end_time'),'state':r['original_market_state']} for i,r in enumerate(rows)],ensure_ascii=False)
    request={'model':model,'messages':[{'role':'user','content':prompt}],'temperature':0,'max_tokens':2500,'reasoning':{'enabled':False}}
    record={'status':'reserved','request':request,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
    path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    try:
        req=Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(request).encode(),headers={'Authorization':'Bearer '+os.environ['OPENROUTER_API_KEY'],'Content-Type':'application/json'})
        with urlopen(req,timeout=150) as response:record['response']=json.loads(response.read(2_000_000))
        record['picks']=parse_ranking(record['response']['choices'][0]['message']['content'],len(rows));record['status']='ranked'
    except Exception as exc:record.update(status='failed',error=type(exc).__name__,detail=str(exc)[:500])
    path.write_text(json.dumps(record,indent=2),encoding='utf-8');return record

def run(inputs,output,model):
    inputs=Path(inputs);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    data=json.loads((inputs/'report.json').read_text(encoding='utf-8'))
    identity={'baseline_sha256':hashlib.sha256((inputs/'report.json').read_bytes()).hexdigest(),'model':model,'pool_cap':60,'additional_search_cap':1,'model_http_cap':1,'question_ids':[c['task_id'] for c in data['cases']]}
    path=output/'manifest.json'
    if path.exists() and json.loads(path.read_text())!=identity:raise ValueError('Experiment identity changed')
    path.write_text(json.dumps(identity,indent=2))
    report={'schema':'nostream_inspired_market_trial_v1','cases':[],'model':model,'tavily_calls':0,'exa_calls':0,'forecast_submissions':False,'equivalence_verified':False}
    for case in data['cases']:
        ident=case['task_id'];payloads=[]
        for p in sorted((inputs/'responses').glob(ident+'-*.json')):
            payload=json.loads(p.read_text(encoding='utf-8')).get('payload')
            if payload:payloads.append(payload)
        query=short_query(case['question'])
        url='https://gamma-api.polymarket.com/public-search?'+urlencode({'q':query,'limit_per_type':20,'page':1,'search_profiles':'false','search_tags':'false'})
        captured,fresh=capture(url,output/f'{ident}-extra-search.json')
        if captured.get('payload'):payloads.append(captured['payload'])
        rows=pool(payloads,case['question']);(output/f'{ident}-pool.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding='utf-8')
        ranking=rank({'title':case['question'],'rules':case.get('resolution_criteria')},rows,output/f'{ident}-rank.json',model) if rows else {'status':'empty_pool','picks':[]}
        picks=[{**rows[p['i']],**p,'eligible_for_edge':False} for p in ranking.get('picks',[])]
        report['cases'].append({'task_id':ident,'question':case['question'],'baseline_candidates':len(case['candidates']),'pool_size':len(rows),'query':query,'search_error':captured.get('error'),'pagination':captured.get('payload',{}).get('pagination'),'ranking_status':ranking['status'],'ranked':picks,'usage':ranking.get('response',{}).get('usage')})
        (output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    report['summary']={'questions':len(report['cases']),'nonempty_pools':sum(c['pool_size']>0 for c in report['cases']),'ranked_calls':sum(c['ranking_status']=='ranked' for c in report['cases']),'failed_calls':sum(c['ranking_status']=='failed' for c in report['cases']),'questions_with_selected_rows':sum(bool(c['ranked']) for c in report['cases'])}
    (output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(report['summary']))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--model',default='nvidia/nemotron-3-super-120b-a12b:free');a=p.parse_args();run(a.inputs,a.output,a.model)
