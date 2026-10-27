"""Mechanical acquisition checks only; no truth, probability or quality score."""
import base64
import hashlib
import json
from collections import Counter
from ForecastAgent.readers.saved import select, version_digest
from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.runtime.acquisition import reading_targets
from ForecastAgent.readers.quality import body_diagnostics
from datetime import datetime, timezone
import re


def acquisition_metrics(bundle):
    """Measure saved material and dates without assigning evidence truth scores."""
    from ForecastAgent.runtime.collection_v2 import eligible
    cutoff=bundle.get('request',{}).get('as_of_utc')
    cutoff=datetime.fromisoformat(cutoff.replace('Z','+00:00')) if cutoff else None
    verified=bundle.get('historical_body_policy')=='verified_snapshots_only'
    from ForecastAgent.runtime.temporal_policy import unrestricted
    if unrestricted(bundle) or bundle.get('mode') == 'live':
        cutoff = None
        verified = False
    details=[]
    for url,page in bundle.get('pages',{}).items():
        diagnostics=page.get('body_diagnostics') or body_diagnostics(page.get('content',''))
        usable=diagnostics['usable_text'] and (not verified or eligible(page,cutoff))
        days=[]
        for row in page.get('rows',[]):
            day=next((str(row[k])[:10] for k in ('date','record_date','publication_date','filingDate','filing_date') if row.get(k)),None)
            if not day and str(row.get('period','')).startswith('M') and row.get('year') and row['period']!='M13':
                day=str(row['year'])+'-'+str(row['period'])[1:]+'-01'
            if day and re.fullmatch(r'\d{4}-\d{2}-\d{2}',day): days.append(day)
        published=page.get('published_at') or page.get('page_date_metadata',{}).get('published_at') or page.get('page_date_metadata',{}).get('extracted_date')
        details.append({'url':url,'usable_body':usable,'state':diagnostics['state'],'chars':len(page.get('content','')),
                        'table_count':diagnostics.get('table_count',0),'reading_gaps':diagnostics.get('page_reading_gaps',[]),
                        'unit':page.get('unit'),'dataset':page.get('dataset'),'row_count':len(page.get('rows',[])),
                        'observed_start':min(days) if days else None,'observed_end':max(days) if days else None,
                        'declared_publication':published,'captured_at':page.get('retrieved_at_utc'),
                        'last_checked_at':page.get('last_checked_at_utc'),'pagination':page.get('pagination'),
                        'requested_range':page.get('requested_range'),'temporal_status':page.get('temporal_status'),
                        'date_warning':'Publication and observation dates alone do not prove historical availability.'})
    needs=[]
    for need in bundle.get('plan') or []:
        urls={e['url'] for e in bundle.get('excerpts',[]) if need['id'] in e.get('need_ids',[])}
        urls.update(a.get('url') for a in bundle.get('fetch_attempts',[]) if need['id'] in a.get('need_ids',[]) and a.get('status')=='completed')
        urls.update(r['url'] for r in bundle.get('dataset_reads',{}).values()
            if need['id'] in r.get('need_ids',[]) and r.get('row_count',0)>0
            and r['url'] in bundle['pages'] and version_digest(bundle['pages'][r['url']])==r.get('source_parsed_sha256'))
        needs.append({'need_id':need['id'],'priority':need.get('priority'),
                      'acquisition_status':bundle.get('need_status', {}).get(need['id'], {}).get('status', 'active'),
                      'usable_associated_sources':[d['url'] for d in details if d['url'] in urls and d['usable_body']],
                      'excerpt_count':sum(need['id'] in e.get('need_ids',[]) for e in bundle.get('excerpts',[]))})
    selected=set(bundle.get('selected_sources',{}))
    return {'schema':'acquisition_metrics_v1','usable_body_count':sum(d['usable_body'] for d in details),
            'audit_only_body_count':sum(not d['usable_body'] for d in details),
            'declared_date_unknown_count':sum(not d['declared_publication'] for d in details),
            'selected_unread_urls':sorted(selected-set(bundle.get('pages',{}))),
            'needs':needs,'sources':details,'truth_verified':False,
            'scope':'Observed capture/excerpt association and date ranges; not semantic adequacy or factual correctness.'}


def collection_acceptance(bundle):
    failures = []; warnings = []; raw_checked = 0
    pages = bundle.get('pages', {})
    source_urls = set(bundle.get('source_leads', {}))
    source_urls.update(canonical_url(hit['url']) for search in bundle.get('searches', []) + bundle.get('exa_searches', []) for hit in search.get('results', []))
    unread = sorted(reading_targets(bundle) - set(pages))
    versions = bundle.get('page_history', {})
    for url, snapshots in [(u, [p]) for u, p in pages.items()] + [(u, ps) for u, ps in versions.items()]:
        for page in snapshots:
            if not page.get('raw_response_base64') or not page.get('sha256'):
                warnings.append({'url': url, 'issue': 'Missing original raw response/hash'})
                continue
            try:
                raw = base64.b64decode(page['raw_response_base64'], validate=True)
                if hashlib.sha256(raw).hexdigest() != page['sha256']:
                    failures.append({'url': url, 'issue': 'Raw response hash mismatch'})
                raw_checked += 1
            except (ValueError, TypeError):
                failures.append({'url': url, 'issue': 'Invalid raw response encoding'})
            if page.get('content_truncated') or page.get('documents_truncated'):
                warnings.append({'url': url, 'issue': 'Parsed source is truncated'})
    for excerpt in bundle.get('excerpts', []):
        try:
            candidates = [pages.get(excerpt['url'], {})] + versions.get(excerpt['url'], [])
            source = next(p for p in candidates if p.get('sha256') == excerpt.get('source_sha256')
                          and (not excerpt.get('source_parsed_sha256') or version_digest(p) == excerpt['source_parsed_sha256']))
            _, text, _ = select({excerpt['url']: source}, excerpt['url'], excerpt['location'].get('document_index'))
            if text[excerpt['start_char']:excerpt['end_char']] != excerpt['text']:
                failures.append({'excerpt': excerpt['id'], 'issue': 'Excerpt coordinates differ from saved version'})
        except (KeyError, StopIteration, ValueError, TypeError):
            failures.append({'excerpt': excerpt.get('id'), 'issue': 'Excerpt source version is unavailable'})
    for market in bundle.get('market_snapshots', {}).values():
        snapshot = market.get('snapshot', {})
        raw = snapshot.get('raw_response')
        if raw is not None:
            digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest != snapshot.get('raw_response_sha256'):
                failures.append({'market': market.get('id'), 'issue': 'Market response hash mismatch'})
        else:
            warnings.append({'market': market.get('id'), 'issue': 'Missing raw market response'})
    searches = bundle.get('searches', [])
    exa = bundle.get('exa_searches', [])
    required_exa = bundle.get('search_policy', {}).get('exa') == 'required'
    exa_attempt_met = any(attempt.get('status') in {'completed', 'failed'} for attempt in exa)
    if required_exa and not exa_attempt_met:
        failures.append({'issue':'Required Exa provider attempt is missing or its outcome is unknown',
                         'provider':'exa', 'reserved_attempts':len(exa)})
    if len(exa) > bundle.get('acquisition_limits',{}).get('exa_search',0):
        failures.append({'issue':'Exa attempt budget exceeded'})
    if any(a.get('status') in {'failed','reserved'} for a in exa):
        warnings.append({'issue':'Failed/interrupted Exa discovery remains'})
    attempts = bundle.get('fetch_attempts', [])
    updates = bundle.get('update_attempts', [])
    if len(searches) > bundle.get('acquisition_limits',{}).get('tavily_basic',3) or len(attempts) > 8 or len(bundle.get('extract_attempts', [])) > 1:
        failures.append({'issue': 'Attempt budget exceeded'})
    if len(updates) > 24 or any(count > 3 for count in Counter(a.get('budget_day') for a in updates).values()):
        failures.append({'issue': 'Update attempt budget exceeded'})
    failed_reads = [a for a in attempts + updates if a.get('status') in {'failed', 'reserved'}]
    failed_searches = [a for a in searches if a.get('status') in {'failed', 'reserved'}]
    from ForecastAgent.runtime.needs import active_needs
    unassociated = [n['id'] for n in active_needs(bundle)
                    if not any(n['id'] in e.get('need_ids', []) for e in bundle.get('excerpts', []))
                    and not any(n['id'] in a.get('need_ids', []) and a.get('status') == 'completed' for a in attempts)
                    and not any(n['id']==row['need_id'] and row['usable_associated_sources'] for row in acquisition_metrics(bundle)['needs'])]
    warnings.extend({'issue': 'Acquisition need has no associated material', 'need_id': n} for n in unassociated)
    if failed_reads:
        warnings.append({'issue': 'Failed/interrupted reads remain', 'count': len(failed_reads)})
    if failed_searches:
        warnings.append({'issue': 'Failed/interrupted searches remain', 'count': len(failed_searches)})
    if not pages and not bundle.get('market_snapshots'):
        warnings.append({'issue': 'No source bodies or market responses were captured'})
    if bundle.get('quarantine'):
        warnings.append({'issue': 'Temporally quarantined material remains', 'count': len(bundle['quarantine'])})
    if unread:
        warnings.append({'issue': 'Discovered sources remain unread', 'count': len(unread)})
    metrics=acquisition_metrics(bundle)
    for need in metrics['needs']:
        if need['priority']=='critical' and need.get('acquisition_status','active')=='active' and not need['usable_associated_sources']:
            warnings.append({'issue':'Critical acquisition need lacks associated usable source','need_id':need['need_id']})
    for row in metrics['sources']:
        if row['reading_gaps'] or row['state'] not in {'readable','thin'}:
            warnings.append({'issue':'Source has extraction gaps','url':row['url'],'state':row['state']})
    status = 'failed' if failures else 'accepted_with_gaps' if warnings else 'accepted'
    from ForecastAgent.evidence.raw_capture import focused, capture_report
    raw_report = capture_report(bundle) if focused(bundle) else None
    interpretation_diagnostics=[]
    if raw_report is not None:
        # Keep all diagnostic warnings, but only mechanical failures gate raw
        # capture acceptance. Comprehension and candidate backlog are separate.
        failures += raw_report['integrity_failures']
        interpretation_issues={'Acquisition need has no associated material',
            'Critical acquisition need lacks associated usable source','Discovered sources remain unread'}
        interpretation_diagnostics=[w for w in warnings if w['issue'] in interpretation_issues]
        warnings=[w for w in warnings if w['issue'] not in interpretation_issues]
        if not raw_report['capture_count']:
            warnings.append({'issue':'No saved raw source material'})
        blocking = (not raw_report['capture_count'] or raw_report['parse_gap_urls']
                    or raw_report['selected_uncaptured_urls'])
        status = 'failed' if failures else 'accepted_with_gaps' if blocking else 'accepted'
    return {'schema': 'collection_acceptance_v1', 'status': status, 'truth_verified': False,
            'acceptance_focus':'raw_capture' if raw_report is not None else 'associated_material',
            'raw_capture_report':raw_report,
            'interpretation_diagnostics':interpretation_diagnostics,
            'raw_versions_checked': raw_checked, 'page_count': len(pages), 'market_snapshot_count': len(bundle.get('market_snapshots', {})),
            'excerpt_count': len(bundle.get('excerpts', [])), 'failures': failures, 'warnings': warnings,
            'acquisition_metrics':metrics,
            'required_searches':{'exa':{'required':required_exa, 'attempt_requirement_met':exa_attempt_met}},
            'source_count': len(source_urls), 'unread_source_count': len(unread), 'unread_urls': unread[:120],
            'captured_unselected_link_count': sum(row.get('origin') == 'page_link' and url not in bundle.get('selected_sources', {})
                                                 for url, row in bundle.get('source_leads', {}).items()),
            'resources': {'tavily_basic_attempts': len(searches), 'exa_search_attempts':len(exa), 'free_http_attempts': len(attempts), 'update_http_attempts': len(updates)},
            'scope': 'Capture integrity and acquisition gaps only; not factual correctness or exhaustive coverage.'}
