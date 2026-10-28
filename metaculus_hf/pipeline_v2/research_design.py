"""Conservative query-design checks and bounded follow-through on observed URLs."""
import re
from urllib.parse import urlsplit
from .contracts import PipelineError

POLICY='goal-aligned-research-query-v1'
BASELINE=re.compile(r'\bhistor\w*|\bprior\b|\bprevious\b|\bseasonal\w*|\bearlier\b',re.I)

def quarter_target(definition):
    m=re.search(r'\bQ([1-4])\s+(20\d\d)\b',definition.get('title',''),re.I)
    return (int(m[1]),int(m[2])) if m else None

def constraints(definition):
    target=quarter_target(definition)
    out=dict(policy=POLICY,one_angle=True,query_must_express_research_goal=True,
        no_scope_broadening='Do not add another competition, stage, metric or population merely to obtain more hits.')
    if target:
        quarter,year=target
        out['quarter_baseline_options']=dict(target=f'Q{quarter} {year}',earlier_quarters=[f'Q{q} {year}' for q in range(1,quarter)],same_quarter_prior_year=f'Q{quarter} {year-1}',
            instruction='If researching a historical baseline, name at least one earlier period or an explicit historical/seasonality search. Do not label a search for only the target result as historical evidence.')
    return out

def alignment_error(plan,state):
    target=quarter_target(state['question'])
    if not target or plan['evidence_kind']!='historical_baseline':return None
    query=plan['query'];intent=plan['research_goal']+' '+plan['why_this_query'];q,y=target
    explicit=re.findall(r'\bQ([1-4])\b',query,re.I);years=re.findall(r'\b20\d\d\b',query)
    target_only=explicit and set(explicit)=={str(q)} and years and set(years)=={str(y)}
    if target_only and BASELINE.search(intent) and not BASELINE.search(query):return 'RESEARCH_QUERY_GOAL_PERIOD_MISMATCH'
    return None

def fallback_query(state,query):
    target=quarter_target(state['question'])
    if not target:return query
    q,y=target
    title=state['question']['title'];title=re.sub(r'\bQ[1-4]\s+20\d\d\b','',title,flags=re.I)
    title=re.sub(r'^(?:how many|how much|what will|will)\s+','',title,flags=re.I)
    title=re.sub(r'\b(?:will|in|its)\b',' ',title,flags=re.I).rstrip(' ?')
    periods=' '.join(f'Q{i}' for i in range(1,q))+' '+str(y) if q>1 else f'Q4 {y-1}'
    return ' '.join((title+' '+periods+' historical baseline official reports').split())[:400]

def reading_candidate(definition,searches,attempted,known_urls):
    """One eligible new research URL; ranking is a lead, never factual approval."""
    from .research_query import is_research
    from .retrieval_plan import recovery_candidates
    from .acquisition import url_identity
    from .platforms import rule_urls
    bases={url_identity(r['url']) for s in searches if not is_research(s) for r in s['results']}
    known={url_identity(u) for u in [*known_urls,*attempted]};rows=[];seen=set()
    # Keep query variants distinct, but prefer another document before rereading
    # the same path with tracking/format parameters. Never rewrite an observed URL.
    known_paths={(urlsplit(u).netloc.lower(),urlsplit(u).path.rstrip('/')) for u in bases|known if u}
    for s in searches:
        if not is_research(s):continue
        for r in s['results']:
            k=url_identity(r['url'])
            if k and k not in bases|known|seen:rows.append(r);seen.add(k)
    eligible=set(recovery_candidates(definition,rows,attempted,limit=len(rows)))
    hosts={(urlsplit(u).hostname or '').removeprefix('www.') for u in definition.get('resolution_source_urls',[])+rule_urls(definition.get('description',''))}
    ranked=[]
    for order,row in enumerate(rows):
        if row['url'] not in eligible:continue
        p=urlsplit(row['url'])
        if re.search(r'https?:/',p.path,re.I):continue
        variant=(p.netloc.lower(),p.path.rstrip('/')) in known_paths
        host=(p.hostname or '').removeprefix('www.');label=p.path+' '+str(row.get('title') or '')
        if host in {'youtube.com','youtu.be','facebook.com','reddit.com','x.com','twitter.com'}:continue
        if re.search(r'/(?:policy|search|about-site|trends-charts)(?:/|$)',p.path,re.I):continue
        official=host in hosts or host.endswith('.gov')
        detail=bool(re.search(r'press.release|earnings.release|production.deliver|/archives/|\.pdf\b|/study/NCT|/data/|report|results|winners|outlook',label,re.I))
        # Generic landing pages are not a concrete additional evidence angle.
        if p.path.rstrip('/') in {'','/press','/investors','/earnings','/data'}:continue
        if not (official or detail):continue
        ranked.append((variant,not official,not detail,order,row['url']))
    return sorted(ranked)[0][-1] if ranked else None
