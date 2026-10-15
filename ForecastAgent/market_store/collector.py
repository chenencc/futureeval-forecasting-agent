"""Fair incremental scans; independent failures never rewrite original forecasts."""
from datetime import datetime,timezone
import json
import time

from .store import append,clock,digest,epoch
from .providers import PublicHTTP,RequestLimit,Retryable,history,venue_status


def save_window(db,key,run_id,points,receipts,start,end,grain,role):
    added=0
    observed=clock()
    with db:
        for point,receipt in points:
            previous=db.execute('SELECT price,bid,ask FROM price_points WHERE market_key=? AND kind=? '
                'AND point_at=? AND resolution_seconds=? ORDER BY observed_at DESC,rowid DESC LIMIT 1',
                (key,point['kind'],point['point_at'],point['resolution_seconds'])).fetchone()
            if previous and tuple(previous)==(point['price'],point.get('bid'),point.get('ask')): continue
            data={**point,'response_sha256':receipt['sha256'],'run_id':str(run_id)}
            at=datetime.fromtimestamp(receipt['received_at_epoch'],timezone.utc).isoformat()
            added+=append(db,'point',key,data,at)
        window={'start_at':start,'end_at':end,'status':'complete_nonempty' if points else 'complete_empty',
            'point_count':len(points),'new_versions':added,'requested_resolution_seconds':grain,
            'empty_is_not_proof_of_no_trading':True,'receipts':receipts,'run_id':str(run_id),'role':role}
        append(db,'window',key,window,observed)
    return window,added


def collect(db,objects,run_id,*,now=None,max_requests=600,seconds=1200,max_markets=1000,http=None):
    now=int(time.time() if now is None else now)
    http=http or PublicHTTP(objects,max_requests,seconds)
    markets=list(db.execute('SELECT m.*,c.data AS cursor_data FROM markets m LEFT JOIN cursors c '
        "ON c.market_key=m.key WHERE COALESCE(c.status,'')!='finalized' ORDER BY COALESCE(c.scanned_until,0),m.key"))
    report={'run_id':str(run_id),'started_at':clock(),'processed':[],'deferred':[],'errors':[],
            'nonfinalized_markets':len(markets)}
    attempted=0
    for row in markets:
        market=dict(row)
        old=json.loads(market.pop('cursor_data') or '{}')
        key=market['key']
        if old.get('status')=='finalized': continue
        if old.get('retry_at',0)>now:
            report['deferred'].append({'key':key,'reason':'retry_deadline','retry_at':old['retry_at']})
            continue
        if attempted>=max_markets:
            report['deferred'].append({'reason':'dispatch_market_bound','remaining':len(markets)-attempted})
            break
        attempted+=1
        saved=old.get('scanned_until')
        start=max(epoch(market['created_at']), (saved-7200) if saved is not None else epoch(market['created_at']))
        # A dispatch processes one day per market. The oldest cursor wins next time.
        end=min(now,start+86400)
        if end<=start: continue
        progress=dict(old)
        try:
            state,state_receipt=venue_status(http,market)
            observed=datetime.fromtimestamp(state_receipt['received_at_epoch'],timezone.utc).isoformat()
            with db:
                append(db,'venue',key,state,observed)
            # Always capture the recent tail before spending capacity on old gaps.
            # The contiguous backfill cursor remains separate from this live tip.
            added=0
            last=old.get('last_point_at') or 0
            live_start=max(epoch(market['created_at']),now-7200)
            if end<now:
                tail,tail_receipts,tail_grain=history(http,market,live_start,now,now)
                _,count=save_window(db,key,run_id,tail,tail_receipts,live_start,now,tail_grain,'live_tail')
                added+=count
                last=max([p['point_at'] for p,_ in tail]+[last])
                progress.update(live_scanned_until=now,last_point_at=last or None,observed_at=clock(),
                    status='backfill',run_id=str(run_id))
                with db: append(db,'cursor',key,progress,progress['observed_at'])
            points,receipts,grain=history(http,market,start,end,now)
            observed=clock()
            last=max([p['point_at'] for p,_ in points]+[last]) or None
            window,count=save_window(db,key,run_id,points,receipts,start,end,grain,'contiguous_backfill')
            added+=count
            # The already scanned live tail closes the remaining contiguous range.
            if progress.get('live_scanned_until')==now and end>=live_start:
                end=now
            with db:
                terminal=state['status'] in {'resolved','resolved_nonbinary_payout'} or (market['venue']=='kalshi' and state['status']=='settled')
                cursor={'scanned_until':max(end,saved or 0),'last_point_at':last,'retry_at':0,
                    'live_scanned_until':now,
                    'status':'finalized' if terminal and end==now else 'caught_up' if end==now else 'backfill',
                    'last_window_status':window['status'],'observed_at':observed,'run_id':str(run_id)}
                append(db,'cursor',key,cursor,observed)
            report['processed'].append({'key':key,**cursor,'new_points':added,'points_returned':len(points)})
        except RequestLimit:
            report['deferred'].append({'key':key,'reason':'dispatch_request_or_time_bound'})
            break
        except Exception as exc:
            # No successful range is fabricated. A failed or truncated range keeps its old cursor.
            wait=exc.retry_after if isinstance(exc,Retryable) else 1800
            observed=clock()
            error={'key':key,'error_type':type(exc).__name__,'reason':str(exc)[:180],
                   'start_at':start,'end_at':end,'retry_at':now+wait}
            with db:
                append(db,'window',key,{**error,'status':'failed','run_id':str(run_id)},observed)
                append(db,'cursor',key,{**progress,'status':'retry_wait','retry_at':now+wait,
                    'last_error':error,'observed_at':observed,'run_id':str(run_id)},observed)
            report['errors'].append(error)
            if isinstance(exc,Retryable) and exc.status==429:
                report['deferred'].append({'reason':'provider_rate_limit','retry_at':now+wait})
                break
    report.update(finished_at=clock(),public_http_attempts=http.requests,
                  model_requests=0,search_requests=0,trades=0,forecasts_submitted=0)
    return report
