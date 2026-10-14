"""Explicit body roles, bounded table views and verified provider document reuse."""
import base64
import copy
import csv
from datetime import date
import hashlib
import io
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

VERSION = 'market-material-contract-v1'


def failure_details(exc):
    """Preserve typed transport reasons; auth denial never triggers browser retry."""
    audit=getattr(exc,'audit',None)
    detail=(audit or {}).get('transport_error',{}).get('detail','') if isinstance(audit,dict) else ''
    code=getattr(exc,'code',None)
    cause=exc
    for _ in range(4):
        if code is not None:break
        cause=getattr(cause,'__cause__',None)
        if cause is None:break
        code=getattr(cause,'code',None)
    match=re.search(r'HTTP (?:Error )?(\d{3})',detail)
    if code is None and match:code=int(match[1])
    category='authentication_required' if code==401 else 'source_removed' if code==410 else 'rate_limited' if code==429 else 'transport_failure'
    return {'http_status':code,'failure_category':category,'automatic_browser_retry_allowed':code not in {401,410,429},
            'transport_audit':audit,'detail':str(exc)[:500]}


def action_link(url):
    """Known sharing/login intents are routing controls, not source documents."""
    p=urlsplit(url);host=(p.hostname or '').lower();path=p.path.lower()
    return ((host in {'api.whatsapp.com','web.whatsapp.com'} and path.rstrip('/')=='/send') or
            (host in {'twitter.com','x.com'} and path.startswith('/intent/')) or
            (host in {'facebook.com','www.facebook.com'} and path in {'/sharer.php','/sharer/sharer.php'}) or
            (host=='t.me' and path.rstrip('/')=='/share/url'))


def url_key(url):
    """Host and fragment normalization; arbitrary path case remains significant."""
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, p.query, ''))


def document_key(url):
    """MND English PLA bulletin IDs have observed case-varied aliases."""
    p = urlsplit(url)
    if p.hostname == 'www.mnd.gov.tw' and not p.query:
        match = re.fullmatch(r'/en/news/plaact/(\d+)/?', p.path, re.I)
        if match:
            return 'mnd:en:plaact:' + match[1]
    return None


def saved_source(bundle, url):
    for key, page in bundle.get('pages', {}).items():
        aliases = [s for s in (key, page.get('url'), page.get('final_url')) if s]
        if any(url_key(url) == url_key(alias) for alias in aliases):
            return page, {'kind': 'saved_exact_or_final_url', 'source_url': page.get('url') or key}
        identity = document_key(url)
        if identity and any(document_key(alias) == identity for alias in aliases):
            # Reuse only a saved readable bulletin, not a previous block/empty page.
            content = page.get('content', '')
            if content and re.search(r'PLA activities|PLA aircraft|ADIZ', content, re.I):
                return page, {'kind': 'verified_provider_document_id', 'document_id': identity,
                    'source_url': page.get('url') or key,
                    'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
                    'source_capture_at': page.get('retrieved_at_utc')}
    return None, None


def sheet_export(url):
    p = urlsplit(url)
    match = re.fullmatch(r'/spreadsheets/d/([A-Za-z0-9_-]+)/edit/?', p.path)
    if p.scheme != 'https' or p.hostname != 'docs.google.com' or not match or p.username:
        return None
    query, fragment = parse_qs(p.query), parse_qs(p.fragment)
    gids = set(query.get('gid', []) + fragment.get('gid', []))
    if len(gids) != 1 or not next(iter(gids)).isdigit():
        return None
    return 'https://docs.google.com/spreadsheets/d/' + match[1] + '/export?' + urlencode(
        {'format': 'csv', 'gid': next(iter(gids))})


def export_access_denied(url, attempts):
    """One workbook export auth denial also prevents alternate-tab export retries."""
    p=urlsplit(url)
    if p.hostname!='docs.google.com' or not p.path.endswith('/export'):return False
    for attempt in attempts:
        other=urlsplit(attempt.get('url',''))
        if other.hostname!=p.hostname or other.path!=p.path:continue
        detail=str(attempt.get('detail',''))+' '+str(attempt.get('error',''))+' '+str(attempt.get('transport_audit',{}))
        if attempt.get('http_status')==401 or attempt.get('failure_category')=='authentication_required' or re.search(r'HTTP (?:Error )?401\b',detail):
            return True
    return False


def sheet_grid_profile(page, url):
    """Read literal public bootstrap metadata; never execute page JavaScript."""
    if sheet_export(url) is None or not page.get('raw_response_base64'):
        return None
    from ForecastAgent.readers.material_structure import html_tree
    try:
        tree=html_tree(page)
        mark='var bootstrapData = '
        script=next((node.text for node in tree.xpath('//script') if mark in (node.text or '') and len(node.text)<2_000_000),None)
        if not script:
            return {'status':'bootstrap_unavailable','complete_target_series_verified':False}
        data,end=json.JSONDecoder().raw_decode(script.split(mark,1)[1])
        tabs=[];extents=[];active=str(data.get('gridId'))
        for value in data.get('changes',{}).values():
            if not isinstance(value,list):continue
            for record in value[:10000]:
                if not isinstance(record,list) or len(record)!=2 or not isinstance(record[1],str):continue
                if record[0] not in {21350203,341438337}:continue
                item=json.loads(record[1])
                if record[0]==21350203:
                    name=item[3][0]['1'][0][2]
                    if (isinstance(name,str) and str(item[2]).isdigit() and
                        isinstance(item[-2],int) and isinstance(item[-1],int)):
                        tabs.append({'gid':str(item[2]),'name':name,'declared_rows':item[-2],'declared_columns':item[-1]})
                elif isinstance(item,dict):
                    for operation in item.get('2',[]):
                        region=operation.get('1')
                        if (isinstance(region,list) and len(region)==5 and str(region[0])==active and
                            all(isinstance(n,int) and n>=0 for n in region[1:])):
                            extents.append({'row_start':region[1],'row_end_exclusive':region[2],
                                'column_start':region[3],'column_end_exclusive':region[4]})
        q=urlsplit(url);gid=(parse_qs(q.query).get('gid') or parse_qs(q.fragment).get('gid') or [None])[0]
        current=next((t for t in tabs if t['gid']==active),None)
        complete=bool(current and any(r['row_start']==0 and r['row_end_exclusive']>=current['declared_rows'] and
             r['column_start']==0 and r['column_end_exclusive']>=current['declared_columns'] for r in extents))
        return {'status':'parsed_literal_metadata','schema':'google_public_sheet_grid_v1','active_gid':active,
            'requested_gid':gid,'requested_tab_matches':gid==active,'active_tab':current,
            'declared_loaded_regions':extents,'declared_grid_extent_loaded':complete,
            'tabs':tabs,'raw_source_sha256':page.get('sha256'),
            'javascript_executed':False,'metadata_extent_is_not_cell_extraction':True,
            'complete_target_series_verified':False}
    except (ValueError,KeyError,TypeError,IndexError,StopIteration) as exc:
        return {'status':'unsupported_bootstrap','error_type':type(exc).__name__,
                'complete_target_series_verified':False,'javascript_executed':False}


def sheet_tab_leads(page,url,*,limit=8):
    """Only saved same-workbook identities can become ordinary public view links."""
    data=sheet_grid_profile(page,url)
    if not data or data.get('status')!='parsed_literal_metadata':return []
    p=urlsplit(url);out=[]
    for tab in data['tabs']:
        if tab['gid']==data['active_gid']:continue
        out.append({'url':urlunsplit((p.scheme,p.netloc,p.path,urlencode({'gid':tab['gid']}),'')),
            'title':tab['name'],'origin':'observed_sheet_tab','parent_url':url,
            'parent_source_sha256':page.get('sha256'),'parent_retrieved_at_utc':page.get('retrieved_at_utc'),
            'observed_grid_id':tab['gid'],'declared_rows':tab['declared_rows'],
            'declared_columns':tab['declared_columns'],'automatic_capture_eligible':True})
    # Compact summaries and definitions fit a bounded visible table better.
    out.sort(key=lambda t:(not bool(re.search('monthly|total|faq|definition',t['title'],re.I)),t['declared_rows'],t['observed_grid_id']))
    return out[:limit]


def _dates(text):
    iso = re.findall(r'\b(20\d{2})[-.](\d{1,2})[-.](\d{1,2})\b', text)
    values = []
    for y, m, d in iso:
        try: values.append(date(int(y), int(m), int(d)))
        except ValueError: pass
    slash = re.findall(r'\b(\d{1,2})/(\d{1,2})/(20\d{2})\b', text)
    # Infer month/day order only when the entire observed sample disambiguates it.
    month_first = any(int(b) > 12 for a, b, y in slash) and not any(int(a) > 12 for a, b, y in slash)
    day_first = any(int(a) > 12 for a, b, y in slash) and not any(int(b) > 12 for a, b, y in slash)
    for a, b, y in slash:
        if month_first or day_first:
            m, d = (a, b) if month_first else (b, a)
            try: values.append(date(int(y), int(m), int(d)))
            except ValueError: pass
    return {'earliest_observed_date': min(values).isoformat() if values else None,
            'latest_observed_date': max(values).isoformat() if values else None,
            'date_literal_count': len(iso) + len(slash),
            'ambiguous_slash_date_order': bool(slash) and not (month_first or day_first)}


def coverage(page, url):
    docs = page.get('documents', [])
    table_docs = [d for d in docs if d.get('metadata', {}).get('format') in {'html_table', 'csv', 'spreadsheet'}]
    viewport = sheet_export(url) is not None
    result = {'schema': VERSION, **_dates(page.get('content', '')),
        'table_document_count': len(table_docs),
        'declared_row_counts': [d['metadata']['row_count'] for d in table_docs if isinstance(d['metadata'].get('row_count'), int)],
        'declared_truncated': bool(page.get('content_truncated') or page.get('documents_truncated') or
            any(d.get('metadata', {}).get('truncated') for d in table_docs)),
        'coverage_status': 'browser_sheet_view_only' if viewport else 'observed_subset' if page.get('content_truncated') else 'completeness_unknown',
        'complete_target_series_verified': False, 'capture_time_is_not_observation_date': True}
    if page.get('data_export_coverage'):
        result.update(page['data_export_coverage'])
    if viewport:
        result.update(recommended_export_url=sheet_export(url), full_dataset_verified=False)
        grid=sheet_grid_profile(page,url)
        if grid:
            result['public_grid_metadata']=grid
            if grid.get('requested_tab_matches') is False:
                result['coverage_status']='requested_tab_not_returned'
            elif grid.get('declared_grid_extent_loaded'):
                result['coverage_status']='captured_declared_grid_extent'
        if page.get('sheet_cell_coverage'):
            result['rendered_cells']=page['sheet_cell_coverage']
            if grid and grid.get('requested_tab_matches'):
                result['coverage_status']=('captured_rendered_grid_extent' if
                    page['sheet_cell_coverage']['all_declared_cells_rendered'] else 'observed_subset')
    return result


def body_contract(page, url, diagnostic):
    result = copy.deepcopy(diagnostic)
    lines=[s.strip().lower() for s in page.get('content','').splitlines() if s.strip()]
    controls={'about','press','copyright','contact us','creators','advertise','developers',
        'terms','privacy','policy & safety','how youtube works','test new features',
        'nfl sunday ticket','home','search','more','sign in','log in','menu'}
    chrome=sum(s in controls or bool(re.fullmatch(r'©?\s*\d{4}\s+\w+(?:\s+\w+)*',s)) for s in lines)
    if len(lines)>=5 and chrome/len(lines)>=.8 and len(page.get('content',''))<1500:
        result.update(state='navigation_or_footer_only',usable_text=False,qualifies_full_body=False)
    if action_link(url) or action_link(page.get('final_url') or url):
        result.update(state='sharing_action_not_original',usable_text=False,qualifies_full_body=False)
    route=urlsplit(url)
    if (route.hostname=='docs.google.com' and route.path.endswith('/export') and
        parse_qs(route.query).get('format')==['csv'] and
        page.get('content_type','').split(';')[0].lower() in {'text/html','application/xhtml+xml'}):
        result.update(state='csv_export_returned_html',usable_text=False,qualifies_full_body=False)
    state = result.get('state', '')
    result['body_role'] = ('blocked_or_unavailable' if not result.get('usable_text') else
        'contextual_lead' if result.get('qualifies_full_body') is False else 'readable_original_scope_unverified')
    if 'navigation' in state or 'footer' in state or 'index' in state:
        result['body_role'] = 'navigation_or_catalog'
    data = coverage(page, url)
    result['data_coverage'] = data
    if result.get('usable_text') and (data['table_document_count'] or sheet_export(url) or page.get('data_export_coverage')):
        result['body_role'] = 'partial_data' if data['coverage_status'] in {'browser_sheet_view_only', 'observed_subset','requested_tab_not_returned'} else 'structured_data_scope_unverified'
        if result.get('usable_text') and result['body_role'] == 'partial_data':
            result['qualifies_full_body'] = False
    result.update(material_contract_version=VERSION, target_coverage_verified=False)
    if result.get('state')=='sharing_action_not_original':
        result['body_role']='sharing_action'
    return result


def materialize_csv(page, url):
    """Save full source bytes; expose bounded rows with original row coordinates."""
    p = urlsplit(url)
    if p.hostname != 'docs.google.com' or not p.path.endswith('/export') or parse_qs(p.query).get('format') != ['csv']:
        return page
    raw64 = page.get('raw_response_base64')
    if not raw64:
        return page
    raw = base64.b64decode(raw64, validate=True)
    if hashlib.sha256(raw).hexdigest() != page.get('sha256'):
        raise ValueError('CSV original byte hash mismatch')
    # Never parse an HTML login/block response as CSV, nor decompress unbounded data.
    kind = page.get('content_type', '').split(';')[0].lower()
    if kind not in {'text/csv', 'application/csv', 'text/plain'} or len(raw) > 4 * 1024**2:
        return page
    if raw.lstrip().startswith((b'<', b'<!')):
        return page
    text = raw.decode('utf-8-sig')
    rows = []
    for index, row in enumerate(csv.reader(io.StringIO(text))):
        if index >= 10000:
            break
        rows.append(row)
    if not rows or len(rows[0]) < 2:
        return page
    indices = sorted(set(range(min(6, len(rows)))) | set(range(max(1, len(rows)-200), len(rows))))
    parts = []; retained = []
    for index in indices:
        part = '[CSV row %s] %s' % (index + 1, ' | '.join(rows[index]))
        if sum(len(s) + 1 for s in parts) + len(part) > 50000:
            break
        parts.append(part); retained.append(index + 1)
    child = copy.deepcopy(page)
    content = '\n'.join(parts)
    partial = len(retained) < len(rows) or len(rows) >= 10000
    child.update(content=content, content_truncated=partial,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        parent_parser_content_sha256=hashlib.sha256(page.get('content','').encode()).hexdigest(),
        documents=[{'page_content':content, 'metadata':{'format':'csv', 'row_count':len(retained),
            'original_row_numbers':retained, 'truncated':partial}}],
        data_export_coverage={'coverage_status':'observed_subset' if partial else 'complete_captured_csv',
            'source_rows_parsed':len(rows), 'source_rows_parse_bound':10000, 'selected_row_count':len(retained),
            'original_row_numbers':retained, 'selection':'first_six_and_last_two_hundred_rows',
            'selected_rows_guaranteed_recent':False, 'original_csv_bytes_preserved':True,
            'full_dataset_verified':False, 'transport_response_complete':not page.get('content_truncated', False),
            'all_row_observed_date_range':_dates(text)})
    return child


def materialize_sheet(page, url):
    """Project saved public cells with coordinates; never infer target completeness."""
    grid=sheet_grid_profile(page,url)
    if not grid or not grid.get('requested_tab_matches') or not grid.get('active_tab'):
        return page
    if page.get('sheet_cell_view',{}).get('raw_source_sha256')==page.get('sha256'):
        return page
    from ForecastAgent.readers.material_structure import html_tree
    tree=html_tree(page);gid=grid['active_gid'];tab=grid['active_tab']
    rows=[];parts=[];periods=[];seen=set();chars=0;bounded=False;occupied={}
    if tab['declared_columns']>1000:return page
    months={m:i+1 for i,m in enumerate(('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'))}
    def column_name(n):
        label=''
        while n:
            n,r=divmod(n-1,26);label=chr(65+r)+label
        return label
    for row in tree.xpath('//table[contains(concat(" ",normalize-space(@class)," ")," waffle ")]//tr'):
        heads=row.xpath('./th[@id]')
        if not heads:continue
        match=re.fullmatch(re.escape(gid)+r'R(\d+)',heads[0].get('id',''))
        if not match:continue
        number=int(match[1])+1
        if number in seen:return page  # Ambiguous overlapping panes need a separate reader.
        inherited=occupied.get(number,{})
        cells=[];column=1;row_periods=[];used=set(inherited)
        for cell in row.xpath('./td'):
            while column in used:column+=1
            try:span=int(cell.get('colspan','1'));height=int(cell.get('rowspan','1'))
            except ValueError:return page
            if not 1<=span<=1000 or not 1<=height<=1000 or column+span-1>1000:return page
            if any(n in used for n in range(column,column+span)):return page
            text=' '.join(cell.text_content().split())
            address=column_name(column)+str(number)
            cells.append({'address':address,'column_start':column,'column_span':span,'row_span':height,'text':text})
            used.update(range(column,column+span))
            for target in range(number+1,number+height):
                pending=occupied.setdefault(target,{})
                if any(n in pending for n in range(column,column+span)):return page
                pending.update({n:address for n in range(column,column+span)})
            period=re.fullmatch(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (20\d{2})',text)
            if period:
                month='%s-%02d'%(period[2],months[period[1]])
                row_periods.append({'address':address,'literal':text,'month':month,
                    'period_in_progress_at_capture':month==str(page.get('retrieved_at_utc',''))[:7],
                    'source_period_finality_verified':False})
            column+=span
        line='[Sheet %s row %s] '%(gid,number)+' | '.join(c['address']+'='+c['text']+
            (' [span %sx%s]'%(c['row_span'],c['column_span']) if c['column_span']>1 or c['row_span']>1 else '') for c in cells if c['text'])
        if len(rows)>=1000 or chars+len(line)>100000:
            bounded=True;break
        chars+=len(line);parts.append(line);seen.add(number);periods.extend(row_periods)
        rows.append({'sheet_row':number,'rendered_column_count':len(used),
            'inherited_merged_cells':{str(k):v for k,v in inherited.items()},'cells':cells})
    if not rows:return page
    complete=(not bounded and len(seen)==tab['declared_rows'] and min(seen)==1 and
        max(seen)==tab['declared_rows'] and all(r['rendered_column_count']==tab['declared_columns'] for r in rows))
    content='\n'.join(parts);child=copy.deepcopy(page)
    child.update(content=content,content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        parent_parser_content_sha256=hashlib.sha256(page.get('content','').encode()).hexdigest(),
        content_truncated=bool(bounded or page.get('content_truncated')),
        sheet_cell_view={'gid':gid,'name':tab['name'],'rows':rows,'raw_source_sha256':page['sha256'],
            'capture_at':page.get('retrieved_at_utc'),'network_calls':0},
        sheet_cell_coverage={'original_row_numbers':sorted(seen),'rendered_row_count':len(rows),
            'row_bound':1000,'character_bound':100000,'projection_truncated':bounded,
            'all_declared_cells_rendered':complete,'full_daily_series_verified':False,
            'earliest_observed_month':min((p['month'] for p in periods),default=None),
            'latest_observed_month':max((p['month'] for p in periods),default=None),
            'month_literal_cells':periods,'source_period_finality_verified':False},
        documents=[{'page_content':content,'metadata':{'format':'spreadsheet','row_count':len(rows),
            'original_row_numbers':sorted(seen),'truncated':bounded,'gid':gid}}])
    return child
