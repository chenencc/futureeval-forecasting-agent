"""Observable capture states and bounded repair routing; no truth adjudication."""
import base64
import hashlib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from ForecastAgent.evidence.source_checks import inspect_body


def capture_status(attempt, page=None, *, now=None):
    page=page or {};now=now or datetime.now(timezone.utc)
    detail=str(attempt.get('detail') or attempt.get('error') or '')
    code=attempt.get('http_status',attempt.get('status_code',page.get('http_status')))
    if not isinstance(code,int):
        match=re.search(r'HTTP(?:\s+Error|Error|\s+status)?[:\s]+(\d{3})',detail,re.I)
        code=int(match[1]) if match else None
    raw='missing'
    if page.get('raw_response_base64'):
        try:
            data=base64.b64decode(page['raw_response_base64'],validate=True)
            raw='verified' if hashlib.sha256(data).hexdigest()==page.get('sha256') else 'hash_mismatch'
        except (ValueError,TypeError):raw='invalid_base64'
    body=inspect_body({'question':''},page.get('url',''),page.get('content',''))['body']
    state=body['state'];usable=body['usable_text']
    recorded=(page.get('body_diagnostics') or {}).get('state')
    if not usable and recorded=='javascript_shell':state='javascript_shell'
    if raw in {'hash_mismatch','invalid_base64'}:
        category,route='raw_integrity_gap','preserve_and_report_corrupt_snapshot';usable=False
    elif usable:category,route='already_recovered','reuse_existing_capture'
    elif code in {401,403} or state in {'access_interstitial','login_preview','login_shell'}:
        category,route='access_restricted','alternate_public_source_or_authorized_access'
    elif code==404:category,route='missing_url','official_feed_sitemap_or_parent_discovery'
    elif code==429:category,route='rate_limited','respect_retry_deadline'
    elif code is not None and code>=500:category,route='service_failure','bounded_later_retry'
    elif 'exceeds size limit' in detail.lower():category,route='size_limit','bounded_streaming_download'
    elif 'codec' in detail.lower() or state=='corrupt_text':category,route='encoding_gap','repair_encoding'
    elif page.get('parse_failure') or 'unsupported content type' in detail.lower():
        category,route='format_or_parser_gap','reparse_saved_bytes_or_add_format_reader'
    elif state=='javascript_shell':category,route='javascript_shell','bounded_browser_render'
    elif state in {'navigation_shell','government_banner_only'}:category,route='index_or_banner','discover_detail_or_structured_endpoint'
    elif state=='empty_text':
        if raw=='verified':category,route='saved_empty_body','reparse_saved_bytes'
        elif detail and not re.search(r'empty|interstitial|blocked',detail,re.I):category,route='transport_or_unknown','inspect_transport_then_bounded_retry'
        else:category,route='empty_or_interstitial_unknown','inspect_saved_response_then_route'
    else:category,route='transport_or_unknown','inspect_transport_then_bounded_retry'
    headers={k.lower():v for k,v in page.get('response_headers',{}).items()}
    deadline=attempt.get('retry_at_utc') or page.get('retry_at_utc')
    retry=headers.get('retry-after')
    if not deadline and retry is not None:
        try:
            captured=datetime.fromisoformat(page.get('retrieved_at_utc','').replace('Z','+00:00'))
            if captured.tzinfo is None:captured=captured.replace(tzinfo=timezone.utc)
        except ValueError:captured=now
        try:deadline=(captured+timedelta(seconds=max(0,int(retry)))).isoformat()
        except (ValueError,TypeError):
            try:deadline=parsedate_to_datetime(retry).isoformat()
            except (ValueError,TypeError):deadline=None
    return {'schema':'capture_status_v1','category':category,'proposed_route':route,'http_status':code,
            'transport_state':'http_error' if code and code>=400 else 'http_success' if code else 'unknown',
            'body_state':state,'usable_text':usable,'raw_state':raw,'saved_raw_available':raw=='verified',
            'parse_failure_present':bool(page.get('parse_failure')),
            'document_reading_gaps':list((page.get('body_diagnostics') or {}).get('page_reading_gaps',[])),
            'content_truncated':bool(page.get('content_truncated')),
            'original_detail':detail,'retry_at_utc':deadline,'truth_verified':False,'execution_authorized':False}


def repair_route(record,attempts,url,*,network=False,historical=False,now=None):
    """One existing repair reservation per URL; no retries or new allowances."""
    now=now or datetime.now(timezone.utc)
    if any(a.get('url')==url for a in attempts):return {'method':None,'reason':'existing_url_reservation'}
    category=record['category']
    deadline=record.get('retry_at_utc')
    if deadline:
        try:
            date=datetime.fromisoformat(deadline.replace('Z','+00:00'))
            if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
            if date>now:return {'method':None,'reason':'retry_deadline_pending'}
        except ValueError:return {'method':None,'reason':'invalid_retry_deadline'}
    if category in {'already_recovered','raw_integrity_gap','access_restricted','missing_url','index_or_banner'}:
        return {'method':None,'reason':record['proposed_route']}
    if record.get('saved_raw_available') and category in {'format_or_parser_gap','encoding_gap','saved_empty_body'}:
        return {'method':'reparse','reason':'reuse_verified_original_bytes'}
    if not network or historical:return {'method':None,'reason':'network_disabled_or_historical_strict'}
    if category=='javascript_shell':return {'method':'browser','reason':'explicit_javascript_shell'}
    if category in {'format_or_parser_gap','size_limit','service_failure','transport_or_unknown'}:
        return {'method':'http','reason':'bounded_existing_http_allowance'}
    return {'method':None,'reason':record['proposed_route']}
