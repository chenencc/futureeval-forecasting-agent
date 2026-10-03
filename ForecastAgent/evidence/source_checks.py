"""Observable acquisition guards preserving original text and uncertainty."""
import copy
import re
from urllib.parse import urlsplit
from ForecastAgent.providers.tavily_search import canonical_url
from ForecastAgent.retrieval_sources import source_urls, allowed_source
from ForecastAgent.readers.quality import body_diagnostics


def issuer_target(question):
    """Infer names only for filing questions; explicit issuer/CIK contracts take precedence."""
    if question.get('target_issuer'):
        return question['target_issuer']
    match=re.search(r'^Will\s+(.+?)\s+(?:file|submit)\b',question.get('question',''),re.I)
    return {'name':match[1],'cik':None,'inferred':True} if match and re.search(r'\b(?:SEC|S-1|IPO|EDGAR)\b',str(question),re.I) else None


def normalized_name(name):
    return ' '.join(re.sub(r'\b(?:incorporated|corporation|corp|inc|pbc|ltd|limited|llc)\b','',
                  re.sub(r'[^a-z0-9]+',' ',name.lower())).split())


def candidate_guard(question,candidate,body=None):
    target=issuer_target(question)
    result={'issuer_status':'not_applicable' if not target else 'unknown','expected_issuer':target,
            'observed_issuer':None,'reason':None,'truth_verified':False}
    if not target:return result
    url=candidate.get('url',''); hostname=(urlsplit(url).hostname or '').lower()
    if hostname not in ('sec.gov','www.sec.gov') or '/Archives/edgar/data/' not in url:return result
    cik=re.search(r'/edgar/data/(\d+)/',url,re.I)
    if target.get('cik') and cik:
        result.update(issuer_status='match' if int(cik[1])==int(target['cik']) else 'mismatch',
                      observed_cik=cik[1],reason='Explicit expected CIK compared with EDGAR path')
        if result['issuer_status']=='mismatch':return result
    observed=candidate.get('issuer_name')
    if body is not None:
        match=re.search(r'([^\n]{1,180})\s*\(Filer\)',body)
        if match:observed=match[1].strip()
        else:
            # SEC submission headers use SGML fields rather than an HTML Filer label.
            filer=re.search(r'\bFILER:\s*(.*?)(?=\b(?:SUBJECT COMPANY|FILED BY|REPORTING-OWNER):|\Z)',body,re.I|re.S)
            company=re.search(r'COMPANY CONFORMED NAME:\s*([^\n\r<]+)',filer[1],re.I) if filer else None
            if company:
                observed=re.split(r'\s+(?:CENTRAL INDEX KEY|ORGANIZATION NAME|IRS NUMBER):',company[1],flags=re.I)[0].strip()
    if observed:
        names=[normalized_name(target['name'])]+[normalized_name(n) for n in target.get('aliases',[])]
        result.update(observed_issuer=observed,issuer_status='match' if normalized_name(observed) in names else 'mismatch',
                      reason='Explicit filer name compared with target and supplied subsidiary aliases')
        if result['issuer_status']=='mismatch' and target.get('inferred') and re.search(r'subsidiar',str(question),re.I):
            result.update(issuer_status='unverified_affiliate',reason='Different filer name; subsidiary exception requires an explicit identity mapping')
    return result


def rule_sources(question,candidates):
    """Reserve exact resolving-rule sources, excluding definitions and background links."""
    keys=set()
    for url in source_urls(question.get('resolution_criteria','')):
        if not allowed_source(url) or re.search(r'/faq|/terms/|/glossary|/definitions',urlsplit(url).path,re.I):continue
        keys.add(canonical_url(url))
    return {c['candidate_id'] for c in candidates if canonical_url(c['url']) in keys}


def inspect_body(question,url,content):
    diagnostic=body_diagnostics(content)
    lines=[s.strip() for s in content.splitlines() if s.strip()]
    long_paragraph=any(len(s)>=160 and len(s.split())>=20 for s in lines)
    numeric_table=sum(bool(re.search(r'\|.*\d.*\|',s)) for s in lines)>=2
    navigation=len(content)<500 and len(lines)>=5 and not long_paragraph and not numeric_table and bool(re.search(
        r'skip to|hoppa|valmynd|leita|main menu|search|navigation|menu',content,re.I))
    login=len(content)<1800 and not long_paragraph and bool(re.search(r'log in(?:to)?|sign in',content,re.I)) and bool(re.search(
        r'password|forgot password|create (?:new )?account',content,re.I))
    if navigation or login:diagnostic.update(state='login_shell' if login else 'navigation_shell',usable_text=False)
    guard=candidate_guard(question,{'url':url},body=content)
    rejected=guard['issuer_status'] in ('mismatch','unverified_affiliate')
    return {'body':diagnostic,'issuer':guard,'eligible_for_evidence':diagnostic['usable_text'] and not rejected,
            'reason':diagnostic['state'] if not diagnostic['usable_text'] else 'issuer_'+guard['issuer_status'] if rejected else None}


def screen_bundle(bundle):
    """Move rejected bodies into an audit inventory, never delete their original text."""
    screened=copy.deepcopy(bundle); exclusions=[]
    for url,page in list(screened.get('pages',{}).items()):
        diagnostic=inspect_body(bundle['request'],url,page.get('content',''))
        page['selection_diagnostics']=diagnostic
        if not diagnostic['eligible_for_evidence']:
            screened.setdefault('selection_excluded_pages',{})[url]=screened['pages'].pop(url)
            exclusions.append({'url':url,'reason':diagnostic['reason'],'diagnostics':diagnostic,'raw_preserved':True})
    screened['selection_acquisition_gaps']=exclusions
    if exclusions:
        gaps=screened.setdefault('result',{}).setdefault('gaps',[])
        for exclusion in exclusions:
            gap='Selection guard: '+exclusion['reason']+' at '+exclusion['url']+'; original preserved.'
            if gap not in gaps:gaps.append(gap)
    return screened
