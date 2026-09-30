"""Bounded model views, conservative date isolation and batched navigation."""
from datetime import datetime
import copy
import json
import re

MONTHS = {m.casefold(): i for i, m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1)}
DATE = re.compile(r'\b(?:\d{4}-\d{2}-\d{2}|(?:'+'|'.join(MONTHS)+r')\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+(?:'+'|'.join(MONTHS)+r')\s+\d{4})\b',re.I)


def late_dates(text, cutoff):
    if not cutoff:
        return []
    dates=[]
    for match in DATE.finditer(text):
        value=match.group()
        try:
            if re.match(r'\d{4}-',value):
                parsed=datetime.strptime(value,'%Y-%m-%d').date()
            else:
                parts=value.replace(',','').split()
                month,day,year=(parts[0],parts[1],parts[2]) if parts[0].casefold() in MONTHS else (parts[1],parts[0],parts[2])
                parsed=datetime(int(year),MONTHS[month.casefold()],int(day)).date()
            if parsed>=cutoff.date(): dates.append(value)
        except ValueError:
            continue
    return dates


def masked(text, cutoff):
    ranges=[]; chars=list(text)
    for match in re.finditer(r'[^\n]+',text):
        dates=late_dates(match.group(),cutoff)
        if dates:
            ranges.append({'start':match.start(),'end':match.end(),'dates':dates})
            chars[match.start():match.end()]=' '*len(match.group())
    return ''.join(chars),ranges


def visible_pages(pages,cutoff):
    result={}
    for url,page in pages.items():
        view=dict(page)
        view['content'],_=masked(page['content'],cutoff)
        view['documents']=[{**d,'page_content':masked(d['page_content'],cutoff)[0]} for d in page.get('documents',[])]
        result[url]=view
    return result


def model_view(value):
    """Keep full tool results on disk; bounded projections go to the model."""
    if isinstance(value,dict):
        result={k:model_view(v) for k,v in value.items() if k not in {'raw_response','raw_response_base64','links','raw_content'}}
        for key in ('content','context'):
            if isinstance(result.get(key),str) and len(result[key])>2400:
                result[key]=result[key][:2400]
                result['model_view_truncated']=True
                result['instruction']='Use read_sources for targeted passages; full text is saved locally.'
        return result
    if isinstance(value,list): return [model_view(x) for x in value[:12]]
    return value


def locate(task,args):
    from ForecastAgent.readers.saved import documents
    from ForecastAgent.tavily_research import canonical_url
    queries=args.get('queries')
    if not isinstance(queries,list) or not 1<=len(queries)<=8:
        raise ValueError('Use one to eight acquisition queries')
    rows=[]
    for query in queries:
        task.needs(query)
        terms=set(re.findall(r'\w+',query.get('query','').casefold()))
        if not terms: raise ValueError('Nonempty passage query required')
        ranked=[]
        for url,page,index,doc in documents(task.bundle['pages']):
            if query.get('url') and canonical_url(query['url'])!=url: continue
            text=doc['page_content']
            for paragraph in re.finditer(r'[^\n]+',text):
                part=paragraph.group()
                if len(part)<100 or late_dates(part,task.cutoff): continue
                score=len(terms & set(re.findall(r'\w+',part.casefold())))
                if score and len(part)<=4000:
                    ranked.append((score,{'url':url,'text':part,'excerpt_args':{'url':url,'document_index':index,
                        'start_char':paragraph.start(),'end_char':paragraph.end(),'need_ids':query['need_ids']}}))
        ranked.sort(key=lambda x:-x[0])
        rows.append({'query':query['query'],'need_ids':query['need_ids'],'passages':[v for _,v in ranked[:2]]})
    return {'located_material':rows,'scope':'Lexical candidates only; associations do not resolve a requirement.'}
