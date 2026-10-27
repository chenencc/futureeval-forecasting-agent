"""Rank observed unread leads for bounded capture, never semantic acceptance."""
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from ForecastAgent.evidence.acquisition_quality import discovery_score
from ForecastAgent.providers.financial import source_urls, allowed_source
from ForecastAgent.tavily_research import canonical_url


def publication_hint(value, url):
    """Normalize heterogeneous publisher dates for routing, not availability."""
    if value:
        for parser in (lambda text:datetime.fromisoformat(text.replace('Z','+00:00')),
                       parsedate_to_datetime):
            try:
                stamp=parser(str(value))
                return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
            except (TypeError,ValueError,OverflowError):
                pass
    match=re.search(r'/(20\d{2})/(\d{2})/(\d{2})/',url)
    return '-'.join(match.groups()) if match else ''


def unread_candidates(bundle, limit=20):
    request = bundle.get('request', {})
    leads = dict(bundle.get('source_leads', {}))
    # Re-decode original question text, never repair an opaque query by guessing.
    for field in ('resolution_criteria', 'fine_print', 'background'):
        for url in source_urls(request.get(field, '')):
            if allowed_source(url):
                leads[canonical_url(url)] = {'url': url, 'origin': 'question_'+field}
    for search in bundle.get('searches', []) + bundle.get('exa_searches', []):
        for hit in search.get('results', []):
            if allowed_source(hit.get('url', '')):
                key = canonical_url(hit['url'])
                leads[key] = {**leads.get(key, {}), **hit}
    attempted = {canonical_url(a.get('url', '')) for a in bundle.get('fetch_attempts', [])}
    captured = {canonical_url(u) for u in bundle.get('pages', {})}
    primary_hosts = {urlsplit(row.get('url', key)).hostname for key, row in leads.items()
                     if row.get('origin', '').startswith('question_')}
    result = []
    for key, row in leads.items():
        url = row.get('url', key)
        if not allowed_source(url) or canonical_url(url) in attempted | captured:
            continue
        origin = row.get('origin', '')
        primary = origin in {'question_resolution_criteria', 'question_fine_print'}
        score = discovery_score(request, url, row.get('title', ''), primary, row.get('published_date'))
        host = urlsplit(url).hostname or ''
        official_host = bool(re.search(r'\.gov(?:\.[a-z]{2})?$', host))
        # Keep short subject identifiers such as AI, GDP and MP as routing hints.
        short_terms = set(re.findall(r'\b[A-Z][A-Z0-9]{1,3}\b', request.get('question','')))
        short_overlap = len(short_terms & set(re.findall(r'\b[A-Z][A-Z0-9]{1,3}\b',
                            (url+' '+row.get('title','')).upper())))
        score += 2*short_overlap
        official_detail = ((host in primary_hosts or official_host) and
                           len(urlsplit(url).path.strip('/').split('/')) >= 2)
        # Domain alone does not make unrelated navigation a capture priority.
        concrete = origin.startswith('question_') or score >= 6 or official_detail and score >= 3
        if not concrete:
            continue
        date_hint = publication_hint(row.get('published_date'),url)
        result.append({'url': url, 'canonical_url': canonical_url(url),
                       'origin': origin or 'saved_search', 'title': row.get('title', ''),
                       'publication_routing_hint': date_hint,
                       'priority': score + 20*primary + 5*origin.startswith('question_') +
                                   5*bool(official_detail and score>=3),
                       'relevance_verified': False})
    # Stable ranking: more recent explicit metadata/URL hints win equal scores.
    # This does not establish publication or historical availability.
    result.sort(key=lambda r:r['canonical_url'])
    result.sort(key=lambda r:r['publication_routing_hint'],reverse=True)
    result.sort(key=lambda r:r['priority'],reverse=True)
    return result[:limit]


def recover_before_stall(task, key):
    """One durable free capture batch. No new model/search or budget reset."""
    b = task.bundle
    if (b['request'].get('recover_sources_before_stall') is not True or task.cutoff or
            b['control'].get('source_recovery_events') or not b.get('plan')):
        return None
    urls = [r['url'] for r in unread_candidates(b, limit=4)][:task.budget()['page_fetch_remaining']]
    if not urls:
        return None
    event = {'urls': urls, 'status': 'reserved', 'budget_reset': False,
             'model_calls': 0, 'search_calls': 0}
    b['control']['source_recovery_events'] = [event]
    task.save()  # Unknown reservations never acquire another recovery allowance.
    try:
        need = next((n for n in b['plan'] if n.get('priority')=='critical'), b['plan'][0])
        query = need.get('query') or need.get('condition') or b['request']['question']
        result = task.execute('read_sources', {'urls': urls,
            'queries':[{'query':query,'need_ids':[need['id']]}], 'rescue_failed': False}, key)
        event['status'] = 'completed_with_gaps' if result.get('error') or not any(
            row.get('ok') for row in result.get('reads', [])) else 'completed'
    except Exception as exc:
        result = {'error': type(exc).__name__}
        event['status'] = 'failed'
    b['transcript'].append({'tool': 'program_source_recovery', 'result': result})
    task.save()
    return result


def rescue_before_close(task, key):
    """One critical basic Extract rescue from its existing reserved allowance."""
    b = task.bundle
    if (not key or task.cutoff or b['request'].get('recover_sources_before_stall') is not True or
            b['control'].get('final_extract_rescue')):
        return None
    from ForecastAgent.runtime.collection_actions import primary_rescue
    candidates = primary_rescue(task)
    if not candidates:
        return None
    event = {'status':'reserved','urls':[r['url'] for r in candidates],
             'model_calls':0,'search_calls':0,'budget_reset':False}
    b['control']['final_extract_rescue'] = event
    task.save()
    try:
        result = task.execute('extract_failed_pages', {'urls':event['urls'],
            'need_ids':sorted({n for r in candidates for n in r['need_ids']}),
            'reason':'Final bounded rescue of failed critical issuer sources using the remaining basic Extract allowance'},key)
        event['status'] = 'completed' if any(
            b['pages'].get(canonical_url(u),{}).get('body_diagnostics',{}).get('usable_text')
            for u in event['urls']) else 'completed_with_gaps'
    except Exception as exc:
        result = {'error':type(exc).__name__,'attempt_preserved':True}
        event['status'] = 'failed'
    b['transcript'].append({'tool':'program_final_extract_rescue','result':result})
    task.save()
    return result
