"""Bounded family-aware market coverage and explicit proposition direction."""
from collections import OrderedDict
from ForecastAgent.polymarket_discovery import candidates
from ForecastAgent.polymarket_match import _similarity
DIRECTIONS={'same','inverse','partial','unknown'}


def build_pool(payloads,title,*,market_cap=120,family_cap=60):
    if not 1<=market_cap<=240 or not 1<=family_cap<=120:
        raise ValueError('Invalid pool bounds')
    families=OrderedDict();seen=set();duplicates=0;raw_markets=0
    # Breadth across queries first; API event order within each query is preserved.
    channels=[]
    for payload in payloads:
        channel=[]
        for event in payload.get('events') or []:
            if not isinstance(event,dict):continue
            key='event:'+str(event.get('id') or event.get('slug') or event.get('title') or '')
            channel.append((key,event,event.get('markets') or []))
        for market in payload.get('markets') or []:
            if isinstance(market,dict):channel.append(('market:'+str(market.get('id') or market.get('conditionId') or market.get('slug') or ''),{},[market]))
        channels.append(channel)
    for index in range(max((len(c) for c in channels),default=0)):
        for channel in channels:
            if index>=len(channel):continue
            key,event,markets=channel[index]
            for market in markets:
                if not isinstance(market,dict):continue
                own=market.get('question') or market.get('title')
                if not own:continue
                parsed=candidates({'markets':[market]},own)
                if not parsed:continue
                raw_markets+=1;row=parsed[0];ident=row['market_id']
                if ident in seen:duplicates+=1;continue
                seen.add(ident)
                row.update(event_title=event.get('title',''),group_item_title=market.get('groupItemTitle'),family_id=key,
                    lexical_score=_similarity(title,own),resolution_source=market.get('resolutionSource'),
                    direction='unknown',equivalence_verified=False,eligible_for_edge=False)
                # Parent descriptions are context, not automatically the child rule.
                row['event_rules']=event.get('description') or ''
                if event.get('slug'):row['market_url']='https://polymarket.com/event/'+event['slug']
                families.setdefault(key,[]).append(row)
    for rows in families.values():
        rows.sort(key=lambda row: -row['lexical_score'])
    selected=[];eligible=list(families.items())[:family_cap]
    # One child per family before additional siblings. This is a prompt coverage
    # allocation, not a semantic ranking or a final forecast evidence selection.
    for level in range(max((len(rows) for _,rows in eligible),default=0)):
        for _,rows in eligible:
            if level<len(rows) and len(selected)<market_cap:selected.append(rows[level])
        if len(selected)>=market_cap:break
    all_rows=[r for rows in families.values() for r in rows]
    selected_ids={r['market_id'] for r in selected}
    return {'schema':'family_pool_v2','rows':selected,'all_rows':all_rows,
        'coverage':{'raw_market_occurrences':raw_markets,'unique_markets':len(all_rows),'duplicates_removed':duplicates,
            'families_discovered':len(families),'families_represented':len({r['family_id'] for r in selected}),
            'selected_markets':len(selected),'omitted_markets':len(all_rows)-len(selected),
            'family_cap':family_cap,'market_cap':market_cap,'pool_complete':len(selected)==len(all_rows)},
        'omitted_market_ids':[r['market_id'] for r in all_rows if r['market_id'] not in selected_ids]}


def annotate_direction(pick,row):
    """Missing evidence remains unknown; no polarity inference from keywords."""
    direction=pick.get('direction','unknown')
    target=pick.get('target_proposition');market=pick.get('market_yes_proposition')
    issues=[]
    if direction not in DIRECTIONS:issues.append('invalid_direction');direction='unknown'
    if direction!='unknown' and (not isinstance(target,str) or not target.strip() or not isinstance(market,str) or not market.strip()):
        issues.append('missing_proposition_evidence');direction='unknown'
    differences=pick.get('differences',[])
    if not isinstance(differences,list):differences=[];issues.append('invalid_differences')
    if direction in {'inverse','partial','unknown'} and pick.get('tier')=='same_quantity_same_date':
        issues.append('top_tier_direction_conflict')
    return {**row,**pick,'direction':direction,'direction_diagnostics':issues,'differences':differences,
        'equivalence_verified':False,'eligible_for_edge':False,'target_yes_probability':None,
        'price_conversion_authorized':False}

