"""Public venue reads with exact frozen outcome binding and bounded pagination."""
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .store import digest

POLY = 'https://data-api.polymarket.com/v2/prices-history'
GAMMA = 'https://gamma-api.polymarket.com/markets/'
KALSHI = 'https://external-api.kalshi.com/trade-api/v2'


class Retryable(RuntimeError):
    def __init__(self, status, retry_after=600):
        self.status,self.retry_after=status,retry_after
        super().__init__('public_provider_http_' + str(status))


class RequestLimit(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PublicHTTP:
    def __init__(self, objects, max_requests=600, seconds=1200):
        self.objects=Path(objects)
        self.objects.mkdir(parents=True,exist_ok=True)
        self.max_requests,self.deadline=max_requests,time.monotonic()+seconds
        self.requests=0
        self.opener=build_opener(NoRedirect())

    def get(self, url, params=None):
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.hostname not in {'data-api.polymarket.com','gamma-api.polymarket.com','clob.polymarket.com','external-api.kalshi.com'}:
            raise ValueError('Non-public venue host rejected')
        if self.requests >= self.max_requests or time.monotonic() >= self.deadline:
            raise RequestLimit('Dispatch request or time limit reached')
        self.requests+=1
        url=url + ('?' + urlencode(params) if params else '')
        started=time.time()
        req=Request(url,headers={'User-Agent':'ForecastAgent-market-history/1.0','Accept':'application/json'})
        try:
            with self.opener.open(req,timeout=min(25,max(1,self.deadline-time.monotonic()))) as response:
                raw=response.read(16*1024**2+1)
                if len(raw)>16*1024**2:
                    raise ValueError('Public response exceeds bound')
        except HTTPError as exc:
            wait=exc.headers.get('Retry-After','600')
            try:
                wait=float(wait) if re.fullmatch(r'\d+(?:\.\d+)?',wait) else parsedate_to_datetime(wait).timestamp()-time.time()
            except (ValueError,TypeError):
                wait=600
            raise Retryable(exc.code,max(60,min(86400,int(wait)))) from None
        received=time.time()
        sha=hashlib.sha256(raw).hexdigest()
        path=self.objects/sha
        if not path.exists(): path.write_bytes(raw)
        value=json.loads(raw)
        if not isinstance(value,dict): raise ValueError('Expected public JSON object')
        return value,{'sha256':sha,'requested_at_epoch':started,'received_at_epoch':received,
                      'url':url,'bytes':len(raw)}


def number(value):
    if isinstance(value,bool): raise ValueError('Boolean price rejected')
    result=float(value)
    if not math.isfinite(result) or not 0<=result<=1:
        raise ValueError('Invalid probability price')
    return result


def stamp(value):
    if isinstance(value,bool): raise ValueError('Boolean timestamp rejected')
    result=float(value)
    if not math.isfinite(result): raise ValueError('Non-finite timestamp')
    if result>1e11: result/=1000
    if result<0: raise ValueError('Negative timestamp')
    return int(result)


def normalize_poly(raw,start,end):
    if not isinstance(raw.get('data'),list): raise ValueError('Missing price data array')
    result=[]
    for row in raw['data']:
        at=stamp(row['timestamp'])
        resolution=row.get('resolution_seconds')
        if type(resolution) is not int or resolution<0 or resolution>86400:
            raise ValueError('Missing or invalid provider resolution')
        # Terminal observations can be unbucketed and clamped to the requested end.
        if not start<=at<end and not (resolution==0 and at==end):
            raise ValueError('Price outside the frozen request window')
        result.append({'point_at':at,'price':number(row['price']),'resolution_seconds':resolution,
            'kind':'provider_unbucketed_terminal_point' if resolution==0 else 'provider_history_price'})
    return result


def normalize_kalshi(raw,ticker,start,end):
    if raw.get('ticker')!=ticker or not isinstance(raw.get('candlesticks'),list):
        raise ValueError('Kalshi history identity/schema mismatch')
    result=[]
    for candle in raw['candlesticks']:
        at=stamp(candle['end_period_ts'])
        if not start<=at<=end: raise ValueError('Candle outside requested window')
        def close(field):
            value=candle.get(field) or {}
            # The current fixed-point dollars schema is explicit; never guess cents.
            return number(value['close_dollars']) if value.get('close_dollars') is not None else None
        bid,ask=close('yes_bid'),close('yes_ask')
        if bid is not None and ask is not None:
            if bid>ask: raise ValueError('Crossed historical book')
            result.append({'point_at':at,'price':(bid+ask)/2,'bid':bid,'ask':ask,
                'resolution_seconds':60,'kind':'candle_bid_ask_close_midpoint'})
        trade=close('price')
        if trade is not None:
            result.append({'point_at':at,'price':trade,'resolution_seconds':60,'kind':'candle_trade_close'})
    return result


def venue_status(http,market):
    if market['venue']=='polymarket':
        raw,receipt=http.get(GAMMA+quote(market['venue_id'],safe=''))
        if str(raw.get('id'))!=market['venue_id']: raise ValueError('Polymarket market identity mismatch')
        def array(field):
            value=raw.get(field,[])
            return json.loads(value) if isinstance(value,str) else value
        outcomes,tokens=array('outcomes'),array('clobTokenIds')
        if len(outcomes)!=2 or {str(x).lower() for x in outcomes}!={'yes','no'} or len(tokens)!=2:
            raise ValueError('Current market is not the frozen binary proposition')
        index=next(i for i,x in enumerate(outcomes) if str(x).lower()=='yes')
        if str(tokens[index])!=market['yes_token'] or (market.get('condition_id') and raw.get('conditionId')!=market['condition_id']):
            raise ValueError('Frozen YES token or condition differs')
        result=None
        state=str(raw.get('umaResolutionStatus') or '').lower()
        status='disputed' if 'disputed' in state else 'closed_awaiting_resolution' if raw.get('closed') else 'open'
        observation={'status':status,'result':result,'raw_sha256':receipt['sha256'],
            'provider_resolution_status':state,'rules_sha256_now':digest(str(raw.get('description') or ''))}
        if raw.get('closed') and status!='disputed' and market.get('condition_id'):
            terminal,final_receipt=http.get('https://clob.polymarket.com/markets/'+quote(market['condition_id'],safe=''))
            tokens=terminal.get('tokens') or []
            yes=[t for t in tokens if str(t.get('outcome','')).lower()=='yes']
            if (terminal.get('condition_id')!=market['condition_id'] or len(tokens)!=2 or
                {str(t.get('outcome','')).lower() for t in tokens}!={'yes','no'} or len(yes)!=1 or
                str(yes[0].get('token_id'))!=market['yes_token']):
                raise ValueError('Explicit settlement identity mismatch')
            winners=[t for t in tokens if t.get('winner') is True]
            if terminal.get('closed') is True and len(winners)==1 and all(type(t.get('winner')) is bool for t in tokens):
                observation.update(status='resolved',result=str(winners[0]['outcome']).lower(),
                    settlement_source='explicit_clob_winner_not_display_price')
            observation.update(gamma_raw_sha256=receipt['sha256'],raw_sha256=final_receipt['sha256'])
            receipt=final_receipt
        return observation,receipt
    raw,receipt=http.get(KALSHI+'/markets/'+quote(market['venue_id'],safe=''))
    value=raw.get('market') or {}
    if value.get('ticker')!=market['venue_id'] or 'kalshi:'+str(value.get('event_ticker'))!=market['family']:
        raise ValueError('Kalshi frozen market identity mismatch')
    settled=value.get('status')=='settled'
    result=value.get('result') if settled and value.get('result') in {'yes','no'} else None
    return {'status':'resolved' if result else str(value.get('status') or 'unknown'),
            'result':result,'raw_sha256':receipt['sha256'],'settlement_ts':value.get('settlement_ts')},receipt


def history(http,market,start,end,now,*,max_pages=8):
    receipts,points=[],[]
    if market['venue']=='polymarket':
        # Conservative retention floors from the official public OpenAPI.
        age=now-start
        grain=60 if age<=6*86400 else 300 if age<=55*86400 else 1800 if age<=85*86400 else 10800
        params={'token_id':market['yes_token'],'start':start,'end':end,'bucket_seconds':grain,'limit':10000}
        seen=set()
        for _ in range(max_pages):
            raw,receipt=http.get(POLY,params)
            receipts.append(receipt)
            points.extend((p,receipt) for p in normalize_poly(raw,start,end))
            pagination=raw.get('pagination',raw)
            if not isinstance(pagination,dict): raise ValueError('Invalid pagination schema')
            if pagination.get('has_more') is False: return points,receipts,grain
            cursor=pagination.get('next_cursor')
            if pagination.get('has_more') is not True or not isinstance(cursor,str) or not cursor or cursor in seen:
                raise ValueError('Incomplete or looping history pagination')
            seen.add(cursor)
            params={**params,'cursor':cursor}
        raise ValueError('History pagination cap; cursor not advanced')
    event=market['family'].split(':',1)[1]
    raw,event_receipt=http.get(KALSHI+'/events/'+quote(event,safe=''))
    value=raw.get('event') or {}
    if value.get('event_ticker')!=event or not value.get('series_ticker'):
        raise ValueError('Kalshi event/series identity mismatch')
    receipts.append(event_receipt)
    ticker=quote(market['venue_id'],safe='')
    url=KALSHI+'/series/'+quote(value['series_ticker'],safe='')+'/markets/'+ticker+'/candlesticks'
    params={'start_ts':start,'end_ts':end,'period_interval':1,'include_latest_before_start':'false'}
    try:
        raw,receipt=http.get(url,params)
    except Retryable as exc:
        if exc.status!=404: raise
        raw,receipt=http.get(KALSHI+'/historical/markets/'+ticker+'/candlesticks',params)
    receipts.append(receipt)
    if raw.get('has_more') or raw.get('cursor') or raw.get('next_cursor'):
        raise ValueError('Unexpected candle pagination; window not complete')
    return [(p,receipt) for p in normalize_kalshi(raw,market['venue_id'],start,end)],receipts,60
