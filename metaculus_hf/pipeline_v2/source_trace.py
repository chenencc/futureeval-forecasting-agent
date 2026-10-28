"""Observed primary-source navigation and bounded, quote-anchored gap searches."""
import re
import hashlib
from copy import deepcopy
from urllib.parse import urlsplit
from .retrieval_plan import folded,rule_identifiers,candidate_exclusion

POLICY='observed-primary-gap-search-v1'
NAV=re.compile(r'/(?:login|signin|sign-in|subscribe|privacy|terms|contact|about|tamogatas|support|donate|newsletter|tags?|category|author|shop|feed)(?:/|$)',re.I)


def anchor_catalog(definition,sources,texts):
    """Bind real snippets in code; the planner selects an ID instead of transcribing text."""
    terms=set(re.findall(r'[a-z0-9]{4,}',folded(definition.get('title',''))))-{'will','would','this','that','before','after'}
    corpus=[('question',definition.get('title','')+'\n'+definition.get('resolution_criteria',''))]
    from .candidate_policy import platform_exclusion
    corpus.extend((s['source_id'],texts.get(s.get('text_sha256')) or '') for s in sources if s.get('status')=='extracted' and not platform_exclusion(s.get('source_url')))
    anchors=[]
    for sid,body in corpus:
        candidates=[]
        for match in re.finditer(r'[^\n]{12,400}',body):
            quote=match.group();content=folded(quote)
            score=sum(t in content for t in terms)
            if score<1:continue
            candidates.append((score,match.start(),match.end(),quote))
        for _,start,end,quote in sorted(candidates,key=lambda x:(-x[0],x[1]))[:3]:
            ident='a'+hashlib.sha256((sid+'\0'+body+'\0'+str(start)+':'+str(end)).encode()).hexdigest()[:16]
            anchors.append(dict(anchor_id=ident,source_id=sid,quote=quote,span=dict(start=start,end=end),
                body_sha256=hashlib.sha256(body.encode()).hexdigest()))
    return anchors


def bind_search_anchor(definition,sources,texts,action):
    result=deepcopy(action);ident=action.get('anchor_id')
    if ident is not None:
        selected=next((x for x in anchor_catalog(definition,sources,texts) if x['anchor_id']==ident),None)
        if selected is None:return result,'SEARCH_ANCHOR_UNBOUND'
        result['anchor']={k:selected[k] for k in ('source_id','quote','span','body_sha256')}
    return result,None


def primary_leads(definition,sources,attempted):
    """An observed link is a lead, never a verified official statement or fact."""
    from .acquisition import url_identity
    from .candidate_policy import platform_exclusion,topic_match
    rule_hosts={urlsplit(u).hostname for u in definition.get('resolution_source_urls',[]) if not platform_exclusion(u)}
    seen={url_identity(u) for u in attempted};leads=[]
    for source in sources:
        if source.get('status')!='extracted' or platform_exclusion(source.get('source_url')):continue
        for link in (source.get('locator') or {}).get('outgoing_links',[]):
            url=link.get('url');key=url_identity(url)
            if key is None or key in seen:continue
            parts=urlsplit(url);host=parts.hostname or '';label=folded(str(link.get('title') or ''))
            if NAV.search(parts.path) or candidate_exclusion(link):continue
            designated=host in rule_hosts;government=host.endswith('.gov') or '.gov.' in host or ('.state.' in host and host.endswith('.us'))
            document=bool(re.search(r'statement|release|report|document|filing|court|police|prosecut|bill|legislation|data|ugyesz|birosag|kozlemeny',label))
            exact_id=any(x.lower() in folded(url+' '+label) for x in rule_identifiers(definition))
            if not ((designated or government) and (document or exact_id or parts.path.lower().endswith('.pdf'))):continue
            if not (exact_id or topic_match(definition,link) or topic_match(definition,dict(url=source.get('source_url'),title=source.get('title')))):continue
            seen.add(key);leads.append(dict(url=url,title=link.get('title'),parent_source_id=source['source_id'],
                lead_kind='designated_source_link' if designated else 'government_domain_link',truth_verified=False))
    return leads[:8]


def gap_search_check(definition,sources,texts,searches,action):
    """Require a concrete gap plus a literal rule/body anchor, never invented site domains."""
    action,error=bind_search_anchor(definition,sources,texts,action)
    if error:return error
    query=action.get('query','');gap=action.get('evidence_gap');anchor=action.get('anchor')
    if not isinstance(gap,str) or not 8<=len(gap.strip())<=500:return 'SEARCH_GAP_REQUIRED'
    if not isinstance(anchor,dict):return 'SEARCH_ANCHOR_REQUIRED'
    sid=anchor.get('source_id');quote=anchor.get('quote')
    if not isinstance(quote,str) or not 12<=len(quote)<=600:return 'SEARCH_ANCHOR_UNBOUND'
    if sid=='question':body=definition.get('title','')+'\n'+definition.get('resolution_criteria','')
    else:
        source=next((s for s in sources if s['source_id']==sid),None)
        body=texts.get(source.get('text_sha256')) if source else None
    if not body or quote not in body:return 'SEARCH_ANCHOR_UNBOUND'
    words=set(re.findall(r'[a-z0-9]{3,}',folded(quote)))-{'this','that','with','from','will','would','market','resolve','official','latest'}
    qwords=set(re.findall(r'[a-z0-9]{3,}',folded(query)))
    if len(words&qwords)<2:return 'SEARCH_ANCHOR_QUERY_MISMATCH'
    known=[*definition.get('resolution_source_urls',[])]
    for source in sources:
        known.extend([source.get('source_url'),source.get('final_url')])
        known.extend(l.get('url') for l in (source.get('locator') or {}).get('outgoing_links',[]))
    known.extend(r.get('url') for s in searches for r in s['results'])
    hosts={urlsplit(u).hostname for u in known if isinstance(u,str)}
    for site in re.findall(r'\bsite:([^\s]+)',query,re.I):
        host=site.strip('"\x27').split('/')[0].lower().removeprefix('*.')
        if not any(h==host or (h or '').endswith('.'+host) for h in hosts):return 'UNOBSERVED_SITE_FILTER'
    return None


def retrieval_progress(definition,sources,texts,rows,attempted,policy):
    from .admission import rejection
    from .retrieval_plan import recovery_candidates
    bodies={s.get('text_sha256') for s in sources if s.get('status')=='extracted' and s.get('text_sha256')
            and not rejection(s,texts.get(s['text_sha256']))}
    urls=set(recovery_candidates(definition,rows,attempted,limit=len(rows),policy=policy))
    urls.update(x['url'] for x in primary_leads(definition,sources,attempted))
    return bodies,urls
