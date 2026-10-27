"""Small deterministic retrieval choices over literal rules and saved URLs."""
import re
import unicodedata
from urllib.parse import urlsplit,unquote,parse_qsl

QUERY_POLICY='independent-event-search-v2'
LEGACY_RULE_POLICY='rule-anchored-dual-search-v1'
ITERATION_POLICY='read-candidates-before-repeat-search-v1'


def search_fingerprint(query):
    """Conservative lexical equivalence; keep dates, domains, negations and case IDs."""
    text=folded(query)
    operators=tuple(sorted(re.findall(r'(?:site|filetype|intitle):[^\s]+|(?<!\w)-[\w.-]+',text)))
    tokens=re.findall(r'[a-z0-9]+',text)
    aliases={'arrested':'arrest','arrests':'arrest','detained':'detain','detention':'detain',
             'investigations':'investigation','statements':'statement','proceeding':'proceedings'}
    words=frozenset(aliases.get(w,w) for w in tokens)
    protected=frozenset(w for w in words if any(c.isdigit() for c in w) or w in {'not','no','without','before','after'})
    return operators,protected,words


def repeated_search(query,searches,provider):
    """Only reuse actual same-provider results; failed/not-dispatched requests stay retryable."""
    op,protected,words=search_fingerprint(query)
    for previous in reversed(searches):
        if previous.get('provider')!=provider or previous.get('status') not in {'ok','empty'}:continue
        oldop,oldprotected,oldwords=search_fingerprint(previous['query'])
        if op!=oldop or protected!=oldprotected:continue
        union=words|oldwords;overlap=words&oldwords
        filler={'latest','news','official','status','statement','update','updates','development','developments','information','current'}
        if words==oldwords or (len(overlap)>=6 and (words^oldwords)<=filler and len(overlap)/max(1,len(union))>=.9):return previous
    return None


def folded(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',unquote(text)) if not unicodedata.combining(c)).lower()


def legal_subject(definition):
    """Only the explicit arrest/detention predicate; no invented case or jurisdiction."""
    title=re.sub(r'^(?:will|would)\s+','',definition.get('title','').strip(),flags=re.I)
    match=re.match(r'^(.{2,100}?)\s+(?:(?:be|get|is|was)\s+)?(?:arrested|detained)\b',title,re.I)
    return match.group(1).strip() if match else None


def independent_query(definition,query,*,provider='tavily',policy=QUERY_POLICY):
    """Guard contaminated legal queries while preserving useful case/native-language refinements."""
    subject=legal_subject(definition) if policy==QUERY_POLICY else None
    if not subject:return query
    text=folded(query)
    if re.search(r'polymarket|kalshi|preduck|prediction.market|\bodds\b|\bbetting\b|\btraders?\b',text) or re.search(r'\b(?:arrested|detained)\s+(?:by|before)\b',text):
        return seed_plan(definition,policy=policy)[provider]
    return query


def candidate_exclusion(row):
    """Exclude explicit market-led navigation, not ordinary reporting that mentions a market."""
    from .admission import rejection
    url=row.get('url') or ''
    if rejection(dict(source_url=url),'candidate')=='MARKET_PRICE_REFERENCE':return 'MARKET_PRICE_REFERENCE'
    lead=folded(url+' '+str(row.get('title') or ''))
    if re.search(r'polymarket|preduck|prediction.market|\bodds\b|traders?.*(?:price|bet)|\bbetting\b',lead):
        return 'MARKET_LED_CANDIDATE'
    return None


def rule_identifiers(definition):
    text=definition.get('resolution_criteria','')+' '+definition.get('title','')
    return list(dict.fromkeys(re.findall(r'\b(?:[A-Z]{1,6}-?\d{2,8}[A-Z]?|[A-Z]{2,8}-\d{1,6})\b',text)))[:8]


def seed_plan(definition,overrides=None,*,policy=QUERY_POLICY):
    from .repository import ensure
    if overrides is not None:
        if isinstance(overrides,(list,tuple)):
            ensure(1<=len(overrides)<=2,'INVALID_SEED_QUERIES')
            result=dict(exa=overrides[0],tavily=overrides[-1])
        else:
            ensure(isinstance(overrides,dict) and set(overrides)=={'exa','tavily'},'INVALID_SEED_QUERIES')
            result=dict(overrides)
        ensure(all(isinstance(q,str) and q.strip() and len(q)<=400 for q in result.values()),'INVALID_SEED_QUERIES')
        return result
    title=definition['title']
    if policy not in (QUERY_POLICY,LEGACY_RULE_POLICY):return dict(exa=title,tavily=title)
    subject=legal_subject(definition) if policy==QUERY_POLICY else None
    if subject:
        return dict(exa=f'"{subject}" criminal investigation prosecutor police court official statement',
                    tavily=f'"{subject}" legal proceedings investigation arrest warrant custody immunity')
    base=re.sub(r'^(?:will|would|is|are|does|do|did|has|have)\s+','',title.strip(),flags=re.I).rstrip(' ?')
    formal=re.search(r'\b(?:[A-Z][A-Za-z\x27’-]*\s+){1,8}(?:Act|Bill|Index|Survey|Report)\b',definition.get('resolution_criteria',''))
    identifiers=rule_identifiers(definition)
    name='"'+formal.group(0)+'"' if formal and formal.group(0).lower() not in base.lower() else ''
    identity=' '.join([name,*identifiers]).strip()
    def clean(value):return ' '.join(value.split())[:400].rstrip()
    return dict(exa=clean(f'{identity} {base} official status'),
                tavily=clean(f'{base} {" ".join(identifiers)} latest developments'))


def recovery_candidates(definition,rows,attempted,limit=2,*,policy=LEGACY_RULE_POLICY):
    """Prioritize exact identifiers, then a different publisher; never invent URLs."""
    from .acquisition import url_identity
    from .admission import rejection
    anchors=rule_identifiers(definition)
    subject=legal_subject(definition) if policy==QUERY_POLICY else None
    entity_terms=set(re.findall(r'[a-z]{3,}',folded(subject or '')))
    terms=set(re.findall(r'[a-z]{4,}',definition.get('title','').lower()))-{'will','would','does','this','that','with','before','after'}
    rule_hosts={urlsplit(u).hostname for u in definition.get('resolution_source_urls',[])}
    seen={url_identity(u) for u in attempted};ranked=[]
    def official(host):return host in rule_hosts or host.endswith('.gov') or '.gov.' in host or ('.state.' in host and host.endswith('.us'))
    for order,row in enumerate(rows):
        url=row.get('url');key=url_identity(url)
        if key is None or key in seen:continue
        seen.add(key);parts=urlsplit(url);host=(parts.hostname or '').lower().removeprefix('www.')
        blocked=candidate_exclusion(row) if subject else rejection(dict(source_url=url),'candidate')=='MARKET_PRICE_REFERENCE'
        if blocked:continue
        if re.search(r'\.(?:png|jpg|jpeg|gif|svg|css|js|woff2?)(?:$|/)',parts.path,re.I):continue
        if re.search(r'/(?:login|sign-in|signin|subscribe|privacy|terms|contact|about|tamogatas|support|donate|newsletter|tags?|category|author|szerzo|rovat|shop|feed)(?:/|$)',parts.path,re.I):continue
        if re.search(r'/search(?:/|$)',parts.path,re.I) and not parts.query:continue
        # A navigation page's ?referer=<article URL> is not evidence of topical relevance.
        identity_query=' '.join(k+'='+v for k,v in parse_qsl(parts.query) if k.lower() not in {'referer','referrer','redirect','return','url','ref'} and not k.lower().startswith('utm_'))
        text=folded(' '.join([parts.netloc+parts.path,identity_query,*[str(row.get(k) or '') for k in ('title','snippet')]]))
        hits=sum(bool(re.search(r'(?<![a-z0-9])'+re.escape(a.lower())+r'(?![a-z0-9])',text)) for a in anchors)
        overlap=sum(t in text for t in terms)
        entity_hits=sum(t in text for t in entity_terms)
        if not hits and (entity_hits<len(entity_terms) if subject else overlap<2):continue
        primary=official(host)
        # Native spelling/order can differ. These are retrieval cues, never legal-stage claims.
        legal_hits=len(set(re.findall(r'prosecut\w*|police|court|investigat\w*|warrant|custody|immun\w*|ugyesz\w*|birosag\w*|nyomoz\w*|letartoztat\w*|orizet\w*',text))) if subject else 0
        lead=folded(url+' '+str(row.get('title') or ''))
        target_in_lead=bool(entity_terms) and all(t in lead for t in entity_terms)
        score=hits*100+overlap+entity_hits*5+min(legal_hits,6)*4+(30 if subject and target_in_lead else 0)+(20 if primary else 0)
        ranked.append(dict(url=url,host=host,official=primary,score=score,order=order))
    ranked.sort(key=lambda x:(-x['score'],x['order']))
    chosen=[]
    while ranked and len(chosen)<limit:
        candidates=ranked
        if chosen:
            distinct=[x for x in ranked if x['host'] not in {z['host'] for z in chosen}]
            independent=[x for x in distinct if not x['official']]
            candidates=independent or distinct or ranked
        selected=candidates[0];chosen.append(selected);ranked.remove(selected)
    return [x['url'] for x in chosen]
