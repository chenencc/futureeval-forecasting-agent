"""Opt-in release collector tools with shared durable fetch reservations."""
import copy
import hashlib
import json
import os
from urllib.parse import urlsplit

from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.runtime.budget import reserve, MAX_INITIAL_HTTP
from ForecastAgent.tools.registry import tool, STRING
from ForecastAgent.supplement.stage import now

POLICY = 'crawl4ai_v1'
MAX_RENDERS = 2
TOOLS = [
    tool('inspect_source_structure', 'Read saved HTML roles, HTML table/JSON/CSV rows or observed iframe/data addresses. No network. offset >= 0; requested limit is 1-100 but each response is capped at TEN items (default 10); use next_offset to continue. Navigation/footer remain archived. Resources are leads, never truth verdicts.',
         {'url': dict(STRING), 'view': {'type':'string','enum':['resources','sections','data']},
          'offset': {'type':'integer','minimum':0}, 'limit': {'type':'integer','minimum':1,'maximum':100}}, ['url','view']),
    tool('follow_source_resource', 'Fetch ONE exact resource_id returned by inspect_source_structure. Shares the existing eight fetch attempts; duplicate successes/failures are not re-fetched. New timestamps and parent hashes are preserved.',
         {'resource_id':dict(STRING),'need_ids':{'type':'array','items':dict(STRING)},'reason':dict(STRING)}, ['resource_id','need_ids','reason']),
    tool('render_source', 'Repair an accepted failed/thin HTML source or observed embedded page with Crawl4AI. At most TWO browser attempts per task, also spending shared eight-fetch slots. 25 dependency requests/20-second deadline. Legacy browser fallback spends a separate reservation; repeated URL attempts do not retry. Not for PDFs or historical captures.',
         {'url':dict(STRING),'need_ids':{'type':'array','items':dict(STRING)},'reason':dict(STRING)}, ['url','need_ids','reason']),
]
NAMES = {t['function']['name'] for t in TOOLS}


def enabled(task):
    return task.bundle['request'].get('source_reading_policy') == POLICY


def configure(task, tools):
    if not enabled(task):
        return tools
    return tools + copy.deepcopy(TOOLS)


def guide():
    return '\nOptional source reading: ordinary read_sources remains first. For empty/thin, dynamic or embedded sources inspect_source_structure, follow an observed resource_id, or render_source when justified by a critical material need. These actions share the eight fetch slots; failures consume reservations. Saved sections/data reads cost no HTTP or search. Inspect dates, station, units and product explicitly; generic rows are not automatically observations. A footer year is not an event date. Use normal saved-document tools to quote exact text. Do not exhaust browser attempts on ordinary pages.\n'


def browser_repairs(task, candidates):
    """Route failed HTML to one bounded free repair before paid extraction."""
    if not enabled(task) or task.bundle['mode'] != 'live':
        return []
    budget = task.budget()
    if budget['page_fetch_remaining'] <= 0 or budget['source_browser_remaining'] <= 0:
        return []
    rows = []
    for row in candidates:
        key = canonical_url(row['url'])
        if any(canonical_url(r.get('url', '')) == key and r.get('channel') == 'source_browser'
               for r in task.bundle['fetch_attempts']):
            continue
        if urlsplit(key).path.lower().endswith(('.pdf', '.json', '.csv', '.xml', '.zip', '.xlsx')):
            continue
        page = task.bundle['pages'].get(key) or next((r.get('page')
            for r in reversed(task.bundle.get('failed_captures', []))
            if canonical_url(r.get('url', '')) == key), None)
        if page and page.get('content_type') not in {'text/html', 'application/xhtml+xml'}:
            continue
        rows.append(row)
    return rows


def source(task, url):
    key = canonical_url(url)
    page = task.bundle['pages'].get(key)
    if not page:
        page = next((r.get('page') for r in reversed(task.bundle.get('failed_captures', []))
                     if canonical_url(r.get('url','')) == key and r.get('page')), None)
    if not page or not page.get('raw_response_base64'):
        raise ValueError('Choose an original saved page or preserved failed HTML capture')
    from ForecastAgent.readers.material_structure import saved_bytes
    saved_bytes(page)
    return key, page


def enrich(task, page):
    if not enabled(task) or not page.get('raw_response_base64'):
        return page
    try:
        if page.get('content_type') in {'application/json','text/csv','application/csv'}:
            from ForecastAgent.readers.material_structure import structured_rows
            result=copy.deepcopy(page)
            result['structured_data']=structured_rows(page)
            return result
        if page.get('content_type') in {'text/html','application/xhtml+xml'}:
            from ForecastAgent.readers.crawl4ai import enrich_html
            return enrich_html(page)
        return page
    except Exception as exc:
        result = copy.deepcopy(page)
        result['source_reading_gap'] = {'error':type(exc).__name__, 'original_body_retained':True}
        return result


def bounded_view(task, url, args):
    from ForecastAgent.readers.material_structure import source_sections, discover_resources, structured_rows
    key, page = source(task, url)
    if task.cutoff:
        from ForecastAgent.runtime.collection_v2 import eligible
        if not eligible(page, task.cutoff):
            raise ValueError('Audit-only current content cannot be read as a historical snapshot')
    offset, requested_limit = args.get('offset',0), args.get('limit',10)
    limit = min(requested_limit,10)
    result = {'url':key,'source_sha256':page['sha256'],'retrieved_at_utc':page.get('retrieved_at_utc'),
              'network_calls':0,'truth_verified':False,'relevance_verified':False,
              'requested_limit':requested_limit,'effective_limit':limit,
              'limit_clamped':requested_limit!=limit}
    supported = ['resources','sections','data'] if page.get('content_type') in {'text/html','application/xhtml+xml'} else ['data']
    result['available_views'] = supported
    if args['view'] not in supported:
        return {**result,'state':'unsupported_view','items':[],'total':0,'next_offset':None,
                'instruction':'Use an available view for this saved source format.'}
    if args['view']=='resources':
        value = discover_resources(page)
        rows=[]
        bank=task.bundle.setdefault('observed_resources',{})
        for r in value['resources']:
            rid=hashlib.sha256(json.dumps([key,page['sha256'],r['url']]).encode()).hexdigest()[:24]
            bank[rid]={'parent_key':key,**r}
            rows.append({'resource_id':rid,**r})
        result.update(items=rows[offset:offset+limit],total=len(rows),truncated=value['truncated'])
        task.save()
    elif args['view']=='sections':
        value=source_sections(page)
        rows=[{**r,'text':r['text'][:1000],'preview_truncated':len(r['text'])>1000} for r in value['sections']]
        result.update(items=rows[offset:offset+limit],total=len(rows),truncated=value['truncated'],
                      explicit_date_metadata=value['explicit_date_metadata'],
                      excluded_reading_roles=value['excluded_roles_remain_archived'])
    else:
        value=structured_rows(page)
        rows=[{'table_path':t['path'],'columns':t['columns'],**r}
              for t in value['tables'] for r in t['rows']]
        result.update(items=rows[offset:offset+limit],total=len(rows),state=value['state'],
                      request_parameters=value['request_parameters'],reported_metadata=value['reported_metadata'],
                      rows_are_not_verified_target_observations=True,truncated=value.get('rows_truncated',False))
    result['next_offset']=offset+limit if offset+limit<result['total'] else None
    # Nested data cells can contain large objects: archive all, bound model output.
    for index, row in enumerate(result['items']):
        if len(json.dumps(row,ensure_ascii=False))>1800:
            result['items'][index]={'preview':json.dumps(row,ensure_ascii=False)[:1500],
                                   'preview_truncated':True, 'source_sha256':page['sha256']}
    return result


def follow(task, args, key):
    if task.bundle['mode']!='live' or task.bundle.get('result'):
        raise ValueError('Observed resource follow-up requires an unfinished live collection')
    record=task.bundle.get('observed_resources',{}).get(args['resource_id'])
    if not record:
        raise ValueError('Use a resource_id returned by inspecting the saved parent')
    _, parent=source(task,record['parent_key'])
    if parent['sha256']!=record['parent_source_sha256']:
        raise ValueError('Observed resource parent changed; inspect its new version')
    from ForecastAgent.retrieval_sources import allowed_source
    if not allowed_source(record['url']):
        raise ValueError('Resource is not an allowed public source')
    task.bundle['source_leads'].setdefault(canonical_url(record['url']),
        {'url':record['url'],'origin':'observed_embedded_resource',
         'parent_url':record['parent_key'],'parent_source_sha256':parent['sha256'],
         'resource_id':args['resource_id'],'need_ids':args['need_ids']})
    task.save()
    try:
        return task.execute('fetch_page',{'url':record['url']},key)
    finally:
        try:
            _,page=source(task,record['url'])
            page['observed_resource_lineage']=copy.deepcopy(record)
            task.save()
        except ValueError:
            pass


def render(task, args):
    if task.bundle['mode']!='live' or task.bundle.get('result'):
        raise ValueError('Browser repair requires an unfinished live collection')
    key=canonical_url(args['url'])
    catalog=task.catalog()
    if key not in catalog and key not in task.bundle['pages']:
        raise ValueError('Render only an accepted exact source URL')
    url=catalog.get(key,{}).get('url',args['url'])
    if any(canonical_url(r.get('url',''))==key and r.get('channel')=='source_browser' for r in task.bundle['fetch_attempts']):
        page=task.bundle['pages'].get(key)
        return {'cached':True,'already_attempted':True,'no_network':True,
                'saved_page':task.page_view(page) if page else None,'gap':'Previous browser reservation is retained; no automatic retry.'}
    saved = task.bundle['pages'].get(key) or next((r.get('page')
        for r in reversed(task.bundle.get('failed_captures', []))
        if canonical_url(r.get('url', '')) == key), None)
    try:
        _,old=source(task,key)
    except ValueError:
        if saved and saved.get('raw_response_base64'):
            raise
        old=None
    if not old and urlsplit(key).path.lower().endswith(('.pdf','.json','.csv','.xml','.zip','.xlsx')):
        raise ValueError('Use the existing document or structured reader for this file type')
    if old and old.get('content_type') not in {'text/html','application/xhtml+xml'}:
        raise ValueError('Use the existing PDF/structured reader, not HTML browser rendering')
    failed=any(canonical_url(r.get('url',''))==key and r.get('status')=='failed' for r in task.bundle['fetch_attempts'])
    embedded=catalog.get(key,{}).get('origin')=='observed_embedded_resource'
    thin=old and (len(old.get('content',''))<1200 or not old.get('body_diagnostics',{}).get('usable_text'))
    if not (failed or embedded or thin):
        raise ValueError('Read the ordinary source first; browser repair is for failed/thin or observed embedded material')
    from ForecastAgent.readers.crawl4ai import require_backend, render_page as candidate
    from ForecastAgent.readers.browser import render_page as legacy
    try:
        require_backend(); backends=[('crawl4ai',candidate),('release_playwright',legacy)]
    except Exception:
        backends=[('release_playwright',legacy)]
    receipts=[]
    def keep_data(records):
        for data in records:
            child=data.get('snapshot')
            if not child: continue
            child['temporal_status']='live_capture'
            if child.get('capture_status',{}).get('usable_text'):
                child['body_diagnostics']={'usable_text':True,'state':'structured_rows'}
                task.store_page(child['url'],child)
            else:
                task.bundle.setdefault('failed_captures',[]).append({'url':child['url'],'page':child,
                    'reason':'Observed browser response is an API/error/metadata gap, not usable observations'})
    for backend, reader in backends:
        count=sum(r.get('channel')=='source_browser' for r in task.bundle['fetch_attempts'])
        if count>=MAX_RENDERS or task.budget()['page_fetch_remaining']<=0:
            break
        attempt={'url':url,'channel':'source_browser','backend':backend,'status':'reserved',
                 'at':now(),'need_ids':args['need_ids'],'reason':args['reason'],
                 'request_limit':25,'timeout_ms':20000}
        reserve(task.bundle,'fetch_attempts',attempt,MAX_INITIAL_HTTP,task.save)
        try:
            options={'retrieved_at':now(),'request_limit':25,'timeout_ms':20000}
            if backend=='crawl4ai':
                options['browser_channel']=os.environ.get('FORECAST_BROWSER_CHANNEL','chromium')
            page=reader(url,**options)
            page['temporal_status']='live_capture'
            # Preserve a complete old body over a weaker partial DOM.
            preserve=old and old.get('body_diagnostics',{}).get('usable_text') and old.get('capture_status',{}).get('render_complete') is not False and page.get('capture_status',{}).get('render_complete') is False
            if preserve:
                task.bundle['page_history'].setdefault(key,[]).append(page)
            else:
                task.store_page(url,page)
            keep_data(page.get('data_response_capture',{}).get('records',[]))
            usable=page.get('capture_status',{}).get('usable_text',page.get('body_diagnostics',{}).get('usable_text',False))
            attempt.update(status='completed' if usable else 'failed',raw_sha256=page['sha256'],
                browser_audit=page.get('browser_audit'),data_response_capture=page.get('data_response_capture'),
                complete_old_body_preserved=bool(preserve))
            if not usable:
                task.bundle.setdefault('failed_captures',[]).append({'url':url,'page':page,'reason':'Browser body remains unreadable'})
            task.save();receipts.append(copy.deepcopy(attempt))
            if usable:
                return {'attempts':[{k:v for k,v in r.items() if k not in {'data_response_capture','browser_audit'}} for r in receipts],
                        'page':task.page_view(task.bundle['pages'][key]),
                        'truth_verified':False,'complete_old_body_preserved':bool(preserve)}
        except Exception as exc:
            audit=getattr(exc,'audit',None) or {}
            keep_data(audit.get('data_response_capture',{}).get('records',[]))
            attempt.update(status='failed',error=type(exc).__name__,detail=str(exc)[:300],
                           browser_audit=audit)
            task.save();receipts.append(copy.deepcopy(attempt))
    return {'attempts':[{k:v for k,v in r.items() if k not in {'data_response_capture','browser_audit'}} for r in receipts],
            'error':'Browser repair unavailable or budget exhausted; preserved failures are not event absence.',
            'fallback_budget_reset':False}


def execute(task, name, args, key):
    if not enabled(task) or task.bundle['pipeline']!='collection':
        raise ValueError('Source tools require the opt-in release collection policy')
    from ForecastAgent.runtime.contracts import check_schema
    schema=next(t['function']['parameters'] for t in TOOLS if t['function']['name']==name)
    check_schema(args,schema,required=True)
    if name=='inspect_source_structure': return bounded_view(task,args['url'],args)
    task.needs(args)
    if name=='follow_source_resource': return follow(task,args,key)
    return render(task,args)
