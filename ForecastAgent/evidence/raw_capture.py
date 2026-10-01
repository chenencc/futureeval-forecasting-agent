"""Mechanical raw acquisition inventory, independent of model interpretation."""
import base64
import hashlib
from urllib.parse import urlsplit
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.tavily_research import canonical_url


def focused(bundle):
    return bundle.get('request',{}).get('acquisition_focus') == 'raw_recall'


def capture_report(bundle):
    rows=[]; failures=[]
    for url,page in bundle.get('pages',{}).items():
        try:
            raw=base64.b64decode(page['raw_response_base64'],validate=True)
            valid=hashlib.sha256(raw).hexdigest()==page.get('sha256')
        except (KeyError,ValueError,TypeError):
            raw=b'';valid=False
        if not valid: failures.append({'url':url,'issue':'Original saved response missing or hash mismatch'})
        body=page.get('content','')
        text_hash=page.get('content_sha256')
        if text_hash and hashlib.sha256(body.encode()).hexdigest()!=text_hash:
            failures.append({'url':url,'issue':'Parsed text hash mismatch'})
        diagnostic=body_diagnostics(body)
        suggested_needs=set()
        for search in bundle.get('searches',[])+bundle.get('exa_searches',[]):
            if any(canonical_url(hit.get('url',''))==canonical_url(url) for hit in search.get('results',[])):
                suggested_needs.update(search.get('need_ids',[]))
        suggested_needs.update(bundle.get('selected_sources',{}).get(url,{}).get('need_ids',[]))
        suggested_needs.update(n for attempt in bundle.get('fetch_attempts',[])
                              if attempt.get('url')==url for n in attempt.get('need_ids',[]))
        from ForecastAgent.readers.datasets import observation_range
        rows.append({'url':url,'host':urlsplit(url).hostname,'raw_bytes':len(raw),
            'raw_sha256':page.get('sha256'),'raw_hash_verified':valid,
            'capture_method':page.get('capture_method','direct_http_or_data_adapter'),
            'vendor_extracted_text':page.get('capture_method')=='tavily_basic_extract',
            'parsed_chars':len(body),'readable_text':diagnostic['usable_text'],
            'parse_state':diagnostic['state'],
            'parse_truncated':bool(page.get('content_truncated') or page.get('documents_truncated')),
            'parsed_document_count':len(page.get('documents',[])),
            **observation_range(page),'candidate_need_ids':sorted(suggested_needs),
            'retrieved_at_utc':page.get('retrieved_at_utc'),
            'published_at':page.get('published_at'),'identity_preview':body[:450],
            'semantic_match_verified':False})
    attempts=bundle.get('fetch_attempts',[])
    unfinished=[a for a in attempts if a.get('status')=='reserved']
    if unfinished:failures.append({'issue':'Unfinished reserved source attempts','count':len(unfinished)})
    failed=[a for a in attempts if a.get('status')=='failed']
    captured={canonical_url(r['url']) for r in rows}
    discovered={canonical_url(hit['url']) for s in bundle.get('searches',[])+bundle.get('exa_searches',[]) for hit in s.get('results',[])}
    failed_snapshots=[]
    for item in bundle.get('failed_captures',[]):
        page=item.get('page',{})
        try:
            raw=base64.b64decode(page['raw_response_base64'],validate=True)
            verified=hashlib.sha256(raw).hexdigest()==page.get('sha256')
        except (KeyError,ValueError,TypeError):
            raw=b'';verified=False
        failed_snapshots.append({'url':item['url'],'raw_bytes':len(raw),'raw_sha256':page.get('sha256'),
            'raw_hash_verified':verified,'reason':item.get('reason'),'parse_failure':page.get('parse_failure'),
            'usable_body':False})
    return {'schema':'raw_capture_report_v1','raw_integrity_passed':bool(rows) and not failures,
        'capture_count':len(rows),'original_response_count':sum(not r['vendor_extracted_text'] for r in rows),
        'vendor_text_count':sum(r['vendor_extracted_text'] for r in rows),
        'readable_body_count':sum(r['readable_text'] for r in rows),
        'total_raw_bytes':sum(r['raw_bytes'] for r in rows),
        'total_parsed_chars':sum(r['parsed_chars'] for r in rows),
        'distinct_host_count':len({r['host'] for r in rows}),
        'parse_gap_urls':[r['url'] for r in rows if not r['readable_text'] or r['parse_truncated']],
        'discovered_url_count':len(discovered),'unfetched_discovery_urls':sorted(discovered-captured),
        'selected_uncaptured_urls':sorted(set(bundle.get('selected_sources',{}))-captured),
        'failed_source_attempts':failed,'integrity_failures':failures,'sources':rows,
        'failed_raw_snapshots':failed_snapshots,
        'scope':'Capture and parse inventory only. Candidate associations, host counts and fetched-discovery ratios are recall proxies, not measured recall, authority or relevance verdicts.',
        'model_summary_required':False,'excerpt_selection_required':False,'semantic_adequacy_verified':False}
