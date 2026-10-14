"""Explicit body roles, bounded table views and verified provider document reuse."""
import base64
import copy
import csv
from datetime import date
import hashlib
import io
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

VERSION = 'market-material-contract-v1'


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
    return result


def body_contract(page, url, diagnostic):
    result = copy.deepcopy(diagnostic)
    if action_link(url) or action_link(page.get('final_url') or url):
        result.update(state='sharing_action_not_original',usable_text=False,qualifies_full_body=False)
    state = result.get('state', '')
    result['body_role'] = ('blocked_or_unavailable' if not result.get('usable_text') else
        'contextual_lead' if result.get('qualifies_full_body') is False else 'readable_original_scope_unverified')
    if 'navigation' in state or 'footer' in state or 'index' in state:
        result['body_role'] = 'navigation_or_catalog'
    data = coverage(page, url)
    result['data_coverage'] = data
    if result.get('usable_text') and (data['table_document_count'] or sheet_export(url) or page.get('data_export_coverage')):
        result['body_role'] = 'partial_data' if data['coverage_status'] in {'browser_sheet_view_only', 'observed_subset'} else 'structured_data_scope_unverified'
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
