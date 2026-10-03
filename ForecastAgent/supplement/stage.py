"""Preserve collection state and produce a separately budgeted evidence overlay."""
import argparse
import base64
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, build_opener

from ForecastAgent.runtime.gap_repair import inventory
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.readers.browser import render_page, BrowserCaptureError
from ForecastAgent.readers.loader import load_response
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.providers.ultra import SafeRedirects, public_url


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def parse_saved(page):
    raw=base64.b64decode(page['raw_response_base64'],validate=True)
    if hashlib.sha256(raw).hexdigest()!=page['sha256']:
        raise ValueError('Saved source hash mismatch')
    if len(raw)>8_000_000:
        raise ValueError('Saved source exceeds repair byte limit')
    kind=page.get('declared_content_type') or page.get('content_type','text/plain')
    url=page.get('url') or page['final_url']
    if kind in {'application/vnd.ms-excel','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'} or raw.startswith((b'\xd0\xcf\x11\xe0',b'PK\x03\x04')):
        result=parse_spreadsheet(raw,url,kind)
        result.update(retrieved_at_utc=page.get('retrieved_at_utc'),
            parent_capture_sha256=page['sha256'])
        return result
    result=load_response({'raw':raw,'url':url,'final_url':page.get('final_url',url),
        'content_type':kind,'charset':page.get('charset','utf-8'),
        'response_headers':page.get('response_headers',{})},
        retrieved_at=page.get('retrieved_at_utc'),preserve_raw_on_failure=True)
    result.update(capture_method='saved_bytes_reparse',parent_capture_sha256=page['sha256'],
        reparsed_at_utc=now())
    return result


def parse_spreadsheet(raw,url,kind):
    documents=[]
    if raw.startswith(b'\xd0\xcf\x11\xe0'):
        import xlrd
        book=xlrd.open_workbook(file_contents=raw,on_demand=True)
        selected=[book.sheet_by_index(i) for i in range(min(book.nsheets,10))]
        sheets=[(s.name,[[str(s.cell_value(r,c))[:1000] for c in range(min(s.ncols,40))] for r in range(min(s.nrows,500))]) for s in selected]
    else:
        # Avoid ZIP bombs before the optional spreadsheet reader decompresses.
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if '[Content_Types].xml' not in z.namelist() or not any(n.startswith('xl/') for n in z.namelist()):
                raise ValueError('ZIP is not an XLSX workbook')
            if sum(i.file_size for i in z.infolist())>30_000_000:
                raise ValueError('Workbook decompression exceeds limit')
        import openpyxl
        book=openpyxl.load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
        sheets=[(s.title,([str(c) if c is not None else '' for c in row] for row in s.iter_rows(max_row=500,max_col=40,values_only=True))) for s in book.worksheets[:10]]
    remaining=150000
    for title,rows in sheets:
        for index,cells in enumerate(rows,1):
            if not any(cells):continue
            if remaining<=0 or len(documents)>=1000:break
            text_part=' | '.join(cells)[:min(4000,remaining)]
            remaining-=len(text_part)
            documents.append({'page_content':text_part,
                'metadata':{'source':url,'sheet':title,'row':index,'format':'spreadsheet'}})
    if hasattr(book,'close'):book.close()
    elif hasattr(book,'release_resources'):book.release_resources()
    text='\n'.join(f"[{d['metadata']['sheet']} row {d['metadata']['row']}] {d['page_content']}" for d in documents)[:150000]
    return {'url':url,'final_url':url,'retrieved_at_utc':None,'reparsed_at_utc':now(),'content_type':kind,
        'sha256':hashlib.sha256(raw).hexdigest(),'raw_response_base64':base64.b64encode(raw).decode(),
        'content':text,'documents':documents,'links':[],'capture_method':'spreadsheet_repair',
        'spreadsheet_reader_version':'bounded_spreadsheet_v1',
        'content_truncated':True,'documents_truncated':True,
        'bounds':{'sheets':10,'rows_per_sheet':500,'columns':40,'formula_values':'formulas_preserved_not_recalculated'},
        'body_diagnostics':body_diagnostics(text)}


class RepairHTTPError(RuntimeError):
    def __init__(self,message,audit):
        super().__init__(message)
        self.audit=audit


def fetch_document(url):
    if not public_url(url):raise ValueError('Not a public source')
    audit={'requests':[{'url':url,'method':'GET'}],'redirect_limit':5}
    class Redirects(SafeRedirects):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            if len(audit['requests'])>=6:raise ValueError('Redirect hop budget exhausted')
            redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
            audit['requests'].append({'url':newurl,'method':'GET','redirect_status':code})
            return redirected
    request=Request(url,headers={'User-Agent':'ForecastAgentSupplement/1'})
    try:
        with build_opener(Redirects()).open(request,timeout=20) as response:
            raw=response.read(8_000_001)
            if len(raw)>8_000_000:raise ValueError('Repair download exceeds byte limit')
            kind=response.headers.get_content_type()
            page={'url':url,'final_url':response.geturl(),'content_type':kind,
                'charset':response.headers.get_content_charset() or 'utf-8',
                'response_headers':{k:response.headers[k] for k in ['Content-Encoding','Last-Modified','ETag'] if k in response.headers},
                'http_audit':audit,'raw_response_base64':base64.b64encode(raw).decode(),
                'sha256':hashlib.sha256(raw).hexdigest(),'retrieved_at_utc':now()}
    except Exception as exc:
        raise RepairHTTPError(str(exc),audit) from exc
    try:
        parsed=parse_saved(page)
        parsed.update(capture_method='supplement_http',retrieved_at_utc=page['retrieved_at_utc'],final_url=page['final_url'],http_audit=audit)
        return parsed
    except Exception as exc:
        page.update(content='',documents=[],links=[],capture_method='supplement_http_parse_failed',
            parse_failure={'type':type(exc).__name__,'message':str(exc)[:300]},body_diagnostics=body_diagnostics(''))
        return page


def run(archive,output,ids,*,network=False,browser_limit=2,http_limit=2):
    if not 1<=len(ids)<=5 or len(set(ids))!=len(ids) or not all(i.isdecimal() for i in ids):
        raise ValueError('Select one to five unique task IDs')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with task_lock(output):
        plan=inventory(Path(archive))
        with zipfile.ZipFile(archive) as source:
            parent_identity={name:hashlib.sha256(source.read(name)).hexdigest()
                for name in ['campaign.json']+[f'tasks/{ident}/bundle.json' for ident in ids]}
        identity={'protocol':'supplement_v1','parent_files_sha256':parent_identity,
            'ids':ids,'network_enabled':network,'browser_limit_per_task':browser_limit,
            'http_limit_per_task':http_limit,'browser_request_limit_per_render':25,
            'source_run_id':__import__('os').environ.get('SUPPLEMENT_SOURCE_RUN_ID')}
        if (output/'manifest.json').exists():
            previous=json.loads((output/'manifest.json').read_text())
            if previous!=identity:
                # Initial pilot bound ZIP packaging; restore binds original member bytes.
                compatible=({k:v for k,v in previous.items() if k!='parent_sha256'} ==
                    {k:v for k,v in identity.items() if k!='parent_files_sha256'})
                old_plan=json.loads((output/'gap-inventory.json').read_text(encoding='utf-8'))
                compatible=compatible and old_plan.get('parent_input_sha256')==plan.get('parent_input_sha256')
                compatible=compatible and [g for g in old_plan['task_gaps'] if g['task_id'] in ids]==[g for g in plan['task_gaps'] if g['task_id'] in ids]
                for ident in ids:
                    child_path=output/'tasks'/ident/'supplement.json'
                    if not child_path.exists():compatible=False;continue
                    old_child=json.loads(child_path.read_text(encoding='utf-8'))
                    compatible=compatible and old_child.get('parent_bundle_sha256')==parent_identity[f'tasks/{ident}/bundle.json']
                if not compatible:raise ValueError('Frozen supplement input or budget changed')
                save(output/'manifest-packaging-migration.json',{'previous':previous,'replacement':identity,
                    'reason':'Bind identical original member bytes across artifact repackaging; retain all reservations and quotas.',
                    'budget_reset':False})
        save(output/'manifest.json',identity);save(output/'gap-inventory.json',plan)
        summary=[]
        with zipfile.ZipFile(archive) as source:
            for ident in ids:
                raw=source.read(f'tasks/{ident}/bundle.json');bundle=json.loads(raw)
                folder=output/'tasks'/ident;folder.mkdir(parents=True,exist_ok=True)
                path=folder/'supplement.json'
                child=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
                    'schema':'evidence_supplement_v1','task_id':ident,
                    'parent_bundle_sha256':hashlib.sha256(raw).hexdigest(),'parent_bundle_json_sha256':digest(bundle),
                    'attempts':[],'captures':{},'original_task_state':next(g['task_state'] for g in plan['task_gaps'] if g['task_id']==ident),
                    'provider_calls':{'models':0,'tavily':0,'exa':0},'truth_verified':False}
                if child['parent_bundle_sha256']!=hashlib.sha256(raw).hexdigest():raise ValueError('Parent changed')
                # Upgrade saved spreadsheet parsing without another network request.
                # Keep the original capture file and account for the local reparse.
                for url,entry in list(child['captures'].items()):
                    captured_path=folder/entry['file']
                    old=json.loads(captured_path.read_text(encoding='utf-8'))
                    if digest(old)!=entry['json_sha256']:raise ValueError('Existing capture changed')
                    if old.get('spreadsheet_reader_version') or not any(d.get('metadata',{}).get('format')=='spreadsheet' for d in old.get('documents',[])):continue
                    if any(a.get('repair_id')=='spreadsheet_reader_v1' and a['url']==url for a in child['attempts']):continue
                    if sum(a['method']=='reparse' for a in child['attempts'])>=8:continue
                    repair={'url':url,'method':'reparse','repair_id':'spreadsheet_reader_v1','status':'reserved','started_at_utc':now(),
                        'source_capture_file':entry['file'],'source_capture_json_sha256':entry['json_sha256']}
                    child['attempts'].append(repair);save(path,child)
                    try:
                        repaired=parse_saved(old)
                        repaired['supplement_provenance']=old['supplement_provenance']
                        key=hashlib.sha256(('spreadsheet_reader_v1'+url).encode()).hexdigest()
                        save(folder/'captures'/f'{key}.json',repaired)
                        readable=repaired.get('body_diagnostics',{}).get('usable_text',False)
                        repair.update(status='captured' if readable else 'unreadable',capture_file=f'captures/{key}.json')
                        child['captures'][url]={'file':repair['capture_file'],'json_sha256':digest(repaired),'readable':readable}
                    except Exception as exc:
                        repair.update(status='failed',error=type(exc).__name__,detail=str(exc)[:500])
                    finally:
                        repair['finished_at_utc']=now();save(path,child)
                captures={r.get('url'):r.get('page') or {} for r in bundle.get('failed_captures',[])}
                captures.update(bundle.get('pages',{}))
                candidates=[r for r in plan['failed_fetches'] if r['task_id']==ident]
                for row in candidates:
                    url=row['url'];category=row['category'];page=captures.get(url,{})
                    if category=='already_recovered' or any(a['url']==url for a in child['attempts']):continue
                    from ForecastAgent.readers.capture_status import repair_route
                    decision=repair_route(row,child['attempts'],url,network=network,
                        historical=(bundle.get('mode') or bundle.get('request',{}).get('mode'))=='historical_strict')
                    method=decision['method']
                    if method is None:continue
                    cap=browser_limit if method=='browser' else http_limit if method=='http' else 8
                    if sum(a['method']==method for a in child['attempts'])>=cap:continue
                    attempt={'url':url,'method':method,'started_at_utc':now(),'status':'reserved','route_reason':decision['reason']}
                    child['attempts'].append(attempt);save(path,child)
                    try:
                        result=parse_saved(page) if method=='reparse' else render_page(url,retrieved_at=now()) if method=='browser' else fetch_document(url)
                        key=hashlib.sha256((method+url).encode()).hexdigest()
                        result['supplement_provenance']={'parent_bundle_sha256':child['parent_bundle_sha256'],'task_id':ident,'method':method}
                        save(folder/'captures'/f'{key}.json',result)
                        from ForecastAgent.readers.capture_status import capture_status
                        observed=capture_status({},result)
                        result['capture_status']=observed
                        result.setdefault('body_diagnostics',{}).update(state=observed['body_state'],usable_text=observed['usable_text'])
                        save(folder/'captures'/f'{key}.json',result)
                        attempt.update(status='captured' if result.get('body_diagnostics',{}).get('usable_text') else 'unreadable',
                            capture_file=f'captures/{key}.json',capture_json_sha256=digest(result),
                            browser_audit=result.get('browser_audit'))
                        if result.get('http_audit'):attempt['http_audit']=result['http_audit']
                        child['captures'][url]={'file':attempt['capture_file'],'json_sha256':digest(result),'readable':attempt['status']=='captured'}
                    except Exception as exc:
                        attempt.update(status='failed',error=type(exc).__name__,detail=str(exc)[:500])
                        if isinstance(exc,BrowserCaptureError):attempt['browser_audit']=exc.audit
                        if isinstance(exc,RepairHTTPError):attempt['http_audit']=exc.audit
                    finally:
                        attempt['finished_at_utc']=now();save(path,child)
                child['remaining_gaps']=[r for r in candidates if r['category']!='already_recovered' and not child['captures'].get(r['url'],{}).get('readable')]
                child['analysis_handoff']={'parent_bundle_sha256':child['parent_bundle_sha256'],
                    'supplement_capture_files':[v['file'] for v in child['captures'].values() if v['readable']],
                    'remaining_gap_count':len(child['remaining_gaps']),
                    'original_reported_gaps':bundle.get('acceptance',{}),
                    'inventory_task_gaps':next(g for g in plan['task_gaps'] if g['task_id']==ident),
                    'relevance_verified':False,'original_task_status_unchanged':True}
                save(path,child)
                summary.append({'task_id':ident,'attempts':len(child['attempts']),
                    'new_readable_captures':sum(v['readable'] for v in child['captures'].values()),
                    'remaining_failed_source_gaps':len(child['remaining_gaps'])})
        save(output/'summary.json',{'schema':'supplement_summary_v1','tasks':summary,
            'provider_calls':{'models':0,'tavily':0,'exa':0},'forecast_submissions':False})
        return summary


def analysis_overlay(bundle,supplement_root,ident):
    """Validate the sidecar and expose new pages without mutating the parent."""
    import copy
    folder=Path(supplement_root)/'tasks'/ident
    child=json.loads((folder/'supplement.json').read_text(encoding='utf-8'))
    if child['task_id']!=ident or child['parent_bundle_json_sha256']!=digest(bundle):
        raise ValueError('Supplement does not belong to this parent bundle')
    overlay=copy.deepcopy(bundle)
    for url,entry in child['captures'].items():
        if not entry['readable']:continue
        path=(folder/entry['file']).resolve()
        if not path.is_relative_to(folder.resolve()):raise ValueError('Invalid capture path')
        page=json.loads(path.read_text(encoding='utf-8'))
        if digest(page)!=entry['json_sha256']:raise ValueError('Supplement capture hash mismatch')
        if not body_diagnostics(page.get('content',''))['usable_text']:raise ValueError('Unreadable supplement')
        overlay.setdefault('pages',{})[url]=page
    overlay['supplement_lineage']=child['analysis_handoff']
    return overlay


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--ids',required=True)
    parser.add_argument('--network',action='store_true')
    args=parser.parse_args();print(json.dumps(run(args.archive,args.output,args.ids.split(','),network=args.network),indent=2))


if __name__=='__main__':main()
