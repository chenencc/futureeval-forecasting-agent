"""Independent, read-only Polymarket discovery replay over acquired questions."""
import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from ForecastAgent.polymarket_match import parse_candidates


def queries(question):
    entities=re.findall(r'\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*)*',question)
    entities=[re.sub(r'^(?:Will|What|How|The)\s+', '', e) for e in entities]
    entities=[e for e in entities if e not in {'Will','What','How','Yes','No','Before','The'} and not re.fullmatch(r'January|February|March|April|May|June|July|August|September|October|November|December',e)]
    entity=' '.join(entities)[:120]
    return list(dict.fromkeys([question[:250],entity or question[:100]]))[:2]


def candidates(payload, question):
    # Closed contracts are discovery evidence, never live executable probabilities.
    normalized=copy.deepcopy(payload); states={}
    rows=list(normalized.get('markets') or [])
    for event in normalized.get('events') or []: rows.extend(event.get('markets') or [])
    for market in rows:
        key=str(market.get('id') or market.get('conditionId') or market.get('slug') or '')
        states[key]={'closed':market.get('closed'), 'active':market.get('active'), 'resolved':market.get('resolved'), 'resolution_source':market.get('resolutionSource')}
        market.update(closed=False,resolved=False,active=True)
    result=parse_candidates(normalized,question,limit=10)
    for row in result:
        state=states[row['market_id']];row['original_market_state']=state
        row['equivalence_verified']=False
        row['time_window_review']='Compare exact dates in both resolution rules; market endDate is not the event deadline.'
        if state['closed'] or state['resolved'] or state['active'] is False:
            row.update(yes_probability_display=None,best_bid=None,best_ask=None,price_status='closed_contract_not_live_quote')
    return result


def capture(url, path):
    if path.exists():
        record=json.loads(path.read_text(encoding='utf-8'))
        if record['endpoint']!=url:raise ValueError('Cache identity mismatch')
        return record,False
    record={'endpoint':url,'captured_at_utc':datetime.now(timezone.utc).isoformat()}
    # Failed requests are preserved and are not silently retried on resume.
    try:
        with urlopen(Request(url,headers={'Accept':'application/json','User-Agent':'ForecastAgent-read-only-discovery/1.0'}),timeout=20) as response:
            raw=response.read(8_000_001)
        if len(raw)>8_000_000:raise ValueError('Response exceeds byte limit')
        record.update(raw_sha256=hashlib.sha256(raw).hexdigest(),payload=json.loads(raw))
    except Exception as exc:record.update(error=type(exc).__name__,detail=str(exc)[:500])
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(record,indent=2,ensure_ascii=False),encoding='utf-8')
    return record,True


def run(source,output,limit=20):
    files=sorted(Path(source).glob('*/bundle.json'))
    if not files:raise ValueError('No acquired bundles found')
    # Spread selection across the frozen ID list rather than choosing matching topics.
    files=[files[i] for i in sorted({int(j*(len(files)-1)/max(1,min(limit,len(files))-1)) for j in range(min(limit,len(files)))})]
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    identity={str(p.parent.name):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    manifest=output/'manifest.json'
    frozen={'source_hashes':identity,'search_cap_per_question':2,'page_cap_per_query':1,'model_calls':0,'tavily_calls':0,'exa_calls':0,'historical_safe':False,'forecast_submissions':False}
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8'))!=frozen:raise ValueError('Frozen input changed')
    manifest.write_text(json.dumps(frozen,indent=2),encoding='utf-8')
    report={'schema':'polymarket_discovery_trial_v1','cases':[],'new_http_attempts':0,'limits':['Lexical candidates require manual entity, event-stage, deadline and resolution-source review.','Current or closed display prices are not historical market probabilities.','Two queries, first page only; no match does not establish absence.']}
    for file in files:
        bundle=json.loads(file.read_text(encoding='utf-8'));req=bundle['request'];question=req['question'];found={};searches=[]
        for index,query in enumerate(queries(question)):
            url='https://gamma-api.polymarket.com/public-search?'+urlencode({'q':query,'limit_per_type':10,'page':1,'search_profiles':'false','search_tags':'false'})
            record,fresh=capture(url,output/'responses'/f'{file.parent.name}-{index}.json');report['new_http_attempts']+=fresh
            searches.append({'query':query,'error':record.get('error'),'pagination':record.get('payload',{}).get('pagination'),'endpoint':url})
            if record.get('payload'):
                for row in candidates(record['payload'],question):found[row['market_id']]=row
        report['cases'].append({'task_id':file.parent.name,'question':question,'resolution_criteria':req.get('resolution_criteria'),'searches':searches,'candidates':sorted(found.values(),key=lambda r:-r['match_score'])[:10],'status':'candidate_found_requires_review' if found else 'no_candidate_in_bounded_search'})
        (output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    report['summary']={'questions':len(files),'with_candidates':sum(bool(c['candidates']) for c in report['cases']),'verified_equivalent':0,'http_errors':sum(bool(s['error']) for c in report['cases'] for s in c['searches'])}
    (output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report['summary']));return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--limit',type=int,default=20)
    args=parser.parse_args()
    if not 1<=args.limit<=100:raise ValueError('Limit must be 1..100')
    run(args.source,args.output,args.limit)

