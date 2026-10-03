"""Observed document dependencies, independent of semantic event judgments."""
import hashlib
import re
from urllib.parse import unquote, urlsplit
from ForecastAgent.evidence.source_identity import page_links
from ForecastAgent.evidence.source_coverage import calendar_dates, observe
from ForecastAgent.supplement.source_contract import contract, match_source

FILES = r'\.(?:pdf|csv|xlsx?|txt|json|xml|zip)$'


def requirements(question, url, page):
    """Readable help/index text is distinct from an acquired target dataset."""
    body=page.get('content',''); links=page_links(page,url)
    coverage=observe(question,url,body,page)
    data_intent=bool(coverage['identity']['expected_ids'] or coverage['clock']['expected'])
    downloads=[x['url'] for x in links if re.search(FILES,urlsplit(x['url']).path,re.I)]
    help_text=bool(re.search(r'download|heruntergeladen|tagesdateien|raw data|rohdaten',body,re.I))
    directory=data_intent and not coverage['data_capture_candidate'] and (bool(downloads) or help_text)
    archives=[x['url'] for x in links if re.search(r'archiv|histor|older|long.?term|langfr|hilfe_dwl',x['url']+' '+x['label'],re.I)]
    window=re.search(r'(?:last|latest|letzten)\s+(\d{1,3})\s*(?:days|tagen)',body,re.I)
    return {'state':'target_data_candidate' if coverage['data_capture_candidate'] else 'directory_only' if directory else 'readable_context',
        'target_data_observed':coverage['data_capture_candidate'],
        'observed_download_urls':downloads,'observed_archive_urls':archives,
        'reported_retention_days':int(window[1]) if window else None,
        'warning':'Directory/help text does not contain the requested observations; follow recorded file or archive links.' if directory else None,
        'truth_verified':False,'timezone_verified':False}


def dependency(question, parent, page, link, *, info=None, match=None):
    """Route an observed link using bounded parent context, never inherit truth."""
    url=link['url']; path=unquote(urlsplit(url).path)
    label=link.get('label',''); info=info or requirements(question,parent,page)
    expected=contract(question)
    match=match or match_source(expected,parent,page.get('content',''))
    same_host=urlsplit(parent).hostname==urlsplit(url).hostname
    days=calendar_dates(question.get('question',''))
    seen_days=calendar_dates(url+' '+label)
    file=bool(re.search(FILES,path,re.I)); target_day=bool(set(days)&set(seen_days))
    parent_day=bool(set(days)&set(calendar_dates(parent)))
    # Broad news feeds and encyclopaedia pages do not lend their topic to every
    # outbound document. Named filing issuers may have long primary releases.
    focused_parent=match['entity_status']=='observed_name' or (len(page.get('content',''))<25000 and match['topic_acceptable'])
    role='ordinary_detail'
    if file and info['state']=='directory_only' and (target_day or parent_day): role='target_data_file'
    elif url in info['observed_archive_urls'] and info['state']=='directory_only': role='archive_navigation'
    elif file and focused_parent and match['entity_acceptable'] and match['quarter_observed'] and match['topic_acceptable']: role='source_attachment'
    elif same_host and len(page.get('content',''))<2500 and re.search(r'\bresults?|scores?|standings\b',label+' '+path,re.I) and (
        match['preferred_domain'] or match_source(expected,url,label)['topic_acceptable']): role='result_summary'
    return {'role':role,'parent_url':parent,'parent_body_sha256':hashlib.sha256(page.get('content','').encode()).hexdigest(),
        'parent_collection_state':info['state'],'parent_topic_matches':match['topic_matches'],
        'target_day_in_route':target_day or parent_day,'observed_route_days':seen_days,
        'different_explicit_day':bool(file and days and seen_days and not target_day),
        'context_inherited_for_routing_only':role!='ordinary_detail','source_identity_verified':False}
