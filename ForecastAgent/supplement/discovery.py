"""Rank only observed public URLs; discovery never establishes equivalence."""
import re
import ipaddress
from urllib.parse import urlsplit, urlunsplit, urljoin

STOP = set("will before after have from this that with more than into according question resolves yes total currently page official service area https http www com org gov html approval approved regulatory regulation authority announced announcement news report reports release issued update updates during about been which would should latest date month year".split())

def tokens(text):
    return {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text) if t.lower() not in STOP}

def safe_url(url):
    try:
        parts=urlsplit(url)
        if parts.username or parts.password or parts.scheme not in {'http','https'}:return None
        host=(parts.hostname or '').lower().rstrip('.')
        if not host or host=='localhost' or host.endswith(('.localhost','.local','.internal')):return None
        try:
            if not ipaddress.ip_address(host).is_global:return None
        except ValueError:pass
        _=parts.port
        # No DNS during planning; the HTTP executor validates resolved addresses.
        return urlunsplit((parts.scheme,parts.netloc,parts.path,parts.query,''))
    except (ValueError,TypeError):return None

def discover(bundle, gaps, limit=3):
    from ForecastAgent.evidence.source_identity import observed_urls, observed_links
    request=bundle.get('request',{})
    criteria=str(request.get('resolution_criteria',''))
    rule_urls=observed_urls(criteria)
    rule_urls=[u for u in (safe_url(u) for u in rule_urls) if u]
    rule_hosts={urlsplit(u).hostname for u in rule_urls}
    context=tokens(str(request.get('question',''))+' '+criteria)
    observed=[]
    def add(url,label,origin,parent=None):
        url=safe_url(url)
        if url:observed.append((url,str(label or ''),origin,parent))
    for field in ['resolution_criteria','background']:
        text=str(request.get(field,''))
        for link in observed_links(text):add(link['url'],link['label'],'question_'+field)
    for search in bundle.get('searches',[])+bundle.get('exa_searches',[]):
        for hit in search.get('results',[]):
            add(hit.get('url'),str(hit.get('title',''))+' '+str(hit.get('content',''))[:2000],'saved_search')
    leads=bundle.get('source_leads',{})
    for lead in (leads.values() if isinstance(leads,dict) else leads):
        if isinstance(lead,dict):add(lead.get('url'),lead.get('title',''),lead.get('origin','saved_lead'),lead.get('parent_url'))
    pages=dict(bundle.get('pages',{}))
    pages.update({c.get('url'):c.get('page',{}) for c in bundle.get('failed_captures',[])})
    for parent,page in pages.items():
        for link in page.get('links',[]):
            if isinstance(link,str):add(urljoin(parent,link),'','saved_page_link',parent)
            elif isinstance(link,dict):add(link.get('url') or link.get('href'),link.get('text',''),'saved_page_link',parent)
    plans=[]
    for gap in gaps:
        if gap['category'] not in {'access_restricted','missing_url','index_or_banner','javascript_shell','format_or_parser_gap','saved_empty_body','transport_or_unknown','size_limit'}:continue
        source=gap['url'];host=urlsplit(source).hostname;ranked={}
        for url,label,origin,parent in observed:
            if url==safe_url(source):continue
            # Platform policy links describe rules, not the event's primary source.
            if urlsplit(url).hostname in {'metaculus.com','www.metaculus.com'} and re.match(r'^/(?:faq|help|accounts)(?:/|$)',urlsplit(url).path,re.I):continue
            if url in pages:
                from ForecastAgent.readers.capture_status import capture_status
                if capture_status({},pages[url])['usable_text']:continue
            candidate_host=urlsplit(url).hostname
            rule=url in rule_urls
            same=candidate_host==host
            overlap=context & tokens(label+' '+urlsplit(url).path)
            if not rule and len(overlap)<2:continue
            if re.search(r'/(?:login|signin|privacy|terms|contact)(?:/|$)',urlsplit(url).path,re.I):continue
            kind='rule_primary_source' if rule else 'same_host_detail' if same else 'public_alternative'
            score=100 if rule else 30 if same else 10
            score+=min(20,len(overlap)*3)+(10 if candidate_host in rule_hosts else 0)
            item={'url':url,'source_gap_url':source,'kind':kind,'score':score,'matched_terms':sorted(overlap),'origin':origin,'observed_parent_url':parent,'rule_source_host':candidate_host in rule_hosts,'equivalence_verified':False,'reason':'Exact rule URL retained' if rule else 'Observed URL with lexical topic overlap'}
            if url not in ranked or score>ranked[url]['score']:ranked[url]=item
        plans.append({'gap_url':source,'category':gap['category'],'candidates':sorted(ranked.values(),key=lambda r:(-r['score'],r['url']))[:limit],'status':'candidates_found' if ranked else 'no_observed_alternative','search_calls':0})
    return {'schema':'supplement_discovery_v1','gaps':plans,'urls_synthesized':False,'truth_verified':False}
