"""Bounded public observations, with availability and revision caveats."""
import base64
import json
import math
import re
from datetime import datetime, date, timedelta, timezone
from urllib.parse import urlencode

CATALOG = {
 'binance_daily': {'publisher':'Binance','unit':'USDT per BTC','scope':'BTCUSDT daily OHLC; USDT is not identical to USD','docs':'https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints'},
 'north_atlantic_sst': {'publisher':'University of Maine Climate Reanalyzer / NOAA OISST v2.1','unit':'degrees Celsius','scope':'North Atlantic 0-60N, 0-80W, latitude-weighted mean; current finalized vintage','docs':'https://climatereanalyzer.org/clim/sst_daily/?dm_id=natlan'},
 'sec_issuers': {'publisher':'SEC','unit':None,'scope':'Ticker/issuer CIK discovery; not exhaustive for private companies','docs':'https://www.sec.gov/search-filings/edgar-application-programming-interfaces'},
 'sec_submissions': {'publisher':'SEC','unit':None,'scope':'Exact CIK submissions, requested forms and acceptance dates; older files require separate bounded requests','docs':'https://www.sec.gov/search-filings/edgar-application-programming-interfaces'},
}


def bounds(args, cutoff):
 start=date.fromisoformat(args['start_date'])
 end=date.fromisoformat(args['end_date'])
 ceiling=(cutoff or datetime.now(timezone.utc)).date()
 limit=20000 if args.get('dataset')=='north_atlantic_sst' else 1000
 if not start<=end<ceiling or (end-start).days>=limit:
  raise ValueError(f'Use a date range before the cutoff UTC day, at most {limit} days')
 return start,end


def request_url(args, cutoff):
 dataset=args['dataset']
 if dataset not in CATALOG: raise ValueError('Unknown dated dataset')
 if dataset=='sec_issuers': return 'https://www.sec.gov/files/company_tickers.json'
 start,end=bounds(args,cutoff)
 if dataset=='binance_daily':
  return 'https://data-api.binance.vision/api/v3/klines?'+urlencode({'symbol':'BTCUSDT','interval':'1d','limit':1000,
   'startTime':int(datetime.combine(start,datetime.min.time(),timezone.utc).timestamp()*1000),
   'endTime':int(datetime.combine(end+timedelta(days=1),datetime.min.time(),timezone.utc).timestamp()*1000)-1})
 if dataset=='north_atlantic_sst':
  return 'https://climatereanalyzer.org/clim/sst_daily/json_2clim/oisst2.1_natlan_sst_day.json'
 cik=str(args.get('cik',''))
 if not re.fullmatch(r'\d{1,10}',cik): raise ValueError('Use a discovered numeric CIK, never infer one')
 if args.get('submission_file'):
  filename=args['submission_file']
  if not re.fullmatch(r'CIK\d{10}-submissions-\d{3}\.json',filename): raise ValueError('Invalid SEC historical submissions filename')
  return 'https://data.sec.gov/submissions/'+filename
 return f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json'


def normalized(page, args, cutoff):
 dataset=args['dataset']; meta=CATALOG[dataset]
 data=json.loads(base64.b64decode(page['raw_response_base64']))
 rows=[]; withheld=0; pagination={'has_more':False}
 if dataset=='sec_issuers':
  query=args.get('query','').strip().casefold()
  if not query or len(query)>150: raise ValueError('Issuer discovery requires an exact name or ticker query')
  rows=[{'cik':str(v['cik_str']),'name':v['title'],'ticker':v['ticker']} for v in data.values()
        if query==str(v['ticker']).casefold() or query in str(v['title']).casefold()][:50]
 else:
  start,end=bounds(args,cutoff)
  if dataset=='binance_daily':
   if not isinstance(data,list): raise ValueError('Exchange did not return candles')
   for v in data:
    opened=datetime.fromtimestamp(v[0]/1000,timezone.utc); closed=datetime.fromtimestamp(v[6]/1000,timezone.utc)
    if not start<=opened.date()<=end or (cutoff and closed>=cutoff): withheld+=1;continue
    values=[float(v[i]) for i in (1,2,3,4)]
    if not all(math.isfinite(x) and x>0 for x in values) or not values[2]<=min(values[0],values[3])<=max(values[0],values[3])<=values[1]:
     raise ValueError('Invalid exchange OHLC row')
    rows.append({'date':opened.date().isoformat(),'available_at':closed.isoformat(),'open':values[0],'high':values[1],'low':values[2],'close':values[3],'volume':float(v[5])})
   pagination={'has_more':len(data)>=1000,'expected_days':(end-start).days+1,'observed_days':len(rows)}
  elif dataset=='north_atlantic_sst':
   if not isinstance(data,list): raise ValueError('SST response must contain year series')
   for series in data:
    name=str(series.get('name',''))
    if not re.fullmatch(r'\d{4}',name): continue
    year=int(name); values=series.get('data',[])
    if len(values)!=366: raise ValueError('Unexpected SST calendar alignment; adapter must be reviewed')
    # Provider indexes raw series by day-of-year (its client calls dateFromDay).
    # A 366-slot non-leap series has a padding slot after December 31.
    for index,value in enumerate(values):
     observed=date(year,1,1)+timedelta(days=index)
     if observed.year!=year: continue
     available=observed+timedelta(days=15)  # 1-day lag plus conservative finalization delay.
     if value is None or not start<=observed<=end or available>=(cutoff or datetime.now(timezone.utc)).date():
      withheld+=1;continue
     numeric=float(value)
     if not math.isfinite(numeric) or not -3<=numeric<=40: raise ValueError('Invalid SST value')
     rows.append({'date':observed.isoformat(),'policy_available_at':available.isoformat(),'sst':numeric})
    rows.sort(key=lambda row:row['date'])
   pagination={'has_more':False,'expected_days':(end-start).days+1,'observed_days':len(rows)}
  else:
   if not args.get('submission_file') and str(int(data.get('cik',-1)))!=str(int(args['cik'])): raise ValueError('SEC CIK differs from request')
   forms=args.get('forms',['S-1','S-1/A'])
   if not isinstance(forms,list) or not 1<=len(forms)<=5 or any(not re.fullmatch(r'[A-Z0-9/-]{1,20}',f) for f in forms):
    raise ValueError('Choose one to five exact SEC forms')
   recent=data if args.get('submission_file') else data.get('filings',{}).get('recent',{})
   required=['form','filingDate','accessionNumber','primaryDocument','acceptanceDateTime']
   if any(not isinstance(recent.get(k),list) for k in required) or len({len(recent[k]) for k in required})!=1:
    raise ValueError('Invalid SEC column lengths')
   for i,form in enumerate(recent['form']):
    if form not in forms: continue
    filed=date.fromisoformat(recent['filingDate'][i]); accepted=datetime.fromisoformat(recent['acceptanceDateTime'][i].replace('Z','+00:00'))
    if accepted.tzinfo is None: raise ValueError('Unknown SEC acceptance timezone')
    if not start<=filed<=end or (cutoff and accepted>=cutoff): withheld+=1;continue
    accession=recent['accessionNumber'][i]; document=recent['primaryDocument'][i]
    if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession) or not re.fullmatch(r'[A-Za-z0-9_.-]+',document): raise ValueError('Invalid SEC document path')
    rows.append({'issuer':data.get('name'),'cik':args['cik'],'form':form,'filing_date':filed.isoformat(),'accepted_at':accepted.isoformat(),
      'url':f"https://www.sec.gov/Archives/edgar/data/{int(args['cik'])}/{accession.replace('-','')}/{document}"})
   pagination={'has_more':bool(data.get('filings',{}).get('files')),'older_files':data.get('filings',{}).get('files',[]),
               'warning':'Empty recent results do not establish absence; older files may contain matching records.'}
 warning='Current provider vintage, not a verified historical snapshot. Dates bound observations, not later corrections. No outcome or absence verdict.'
 if dataset=='north_atlantic_sst': warning+=' The 15-day delay is a conservative policy assumption, not verified publication provenance.'
 content=json.dumps({'dataset':dataset,**meta,'warning':warning})+'\n'+'\n'.join(json.dumps(row,sort_keys=True) for row in rows)
 page.update(dataset=dataset,publisher=meta['publisher'],unit=meta['unit'],rows=rows,withheld_rows=withheld,pagination=pagination,
  temporal_status='date_bounded_current_data',data_warning=warning,capture_method='dated_data',dated_request=args,
  content=content[:150000],content_truncated=len(content)>150000,
  documents=[{'page_content':json.dumps(row,sort_keys=True),'metadata':{'format':'dated_data_row','dataset':dataset}} for row in rows[:1000]],
  documents_truncated=len(rows)>1000,document_count=len(rows),links=[r['url'] for r in rows if 'url' in r])
 return page
