"""Platform metadata adapters only; both feed the exact same forecasting engine."""
from __future__ import annotations
import json
from urllib.parse import urlsplit
import re
import math
from datetime import datetime,timezone
from .repository import ensure


def _array(value):return json.loads(value) if isinstance(value,str) else value or []


def rule_urls(text):
    """Remove prose punctuation, retaining explicit Markdown/angle URL targets.

    Original rule text is preserved; percent-encoded punctuation is untouched.
    """
    values=[]
    for match in re.finditer(r'https?://[^\s<>\)\]\"]+',text):
        url=match.group()
        if match.start()==0 or text[match.start()-1] not in '(<"':url=url.rstrip('.,;:!?')
        if url and url not in values:values.append(url)
    return values


def metaculus(post):
    """Expand each leaf question, preserving its real question ID and parent post ID."""
    leaves=[post["question"]] if post.get("question") else post.get("group_of_questions",{}).get("questions",[])
    ensure(bool(leaves),"METACULUS_QUESTION_MISSING")
    results=[]
    for question in leaves:
        kind={"multiple_choice":"categorical","numeric":"numeric","date":"date","binary":"binary"}.get(question.get("type"))
        ensure(kind is not None,"UNSUPPORTED_QUESTION_TYPE")
        rules=question.get("resolution_criteria") or post.get("resolution_criteria")
        ensure(isinstance(rules,str) and bool(rules.strip()),"MISSING_RESOLUTION_RULES")
        for extra in (question.get("fine_print"),post.get("fine_print")):
            ensure(extra is None or isinstance(extra,str),"INVALID_RESOLUTION_FINE_PRINT")
            if extra and extra.strip() and extra not in rules:
                rules+="\n\nAdditional resolution conditions:\n"+extra
        scale={}
        if kind in {"numeric","date"}:
            scaling=dict(question.get("scaling") or {})
            grid=question.get("forecast_grid") or scaling.pop("continuous_range",None)
            lo,hi=scaling.get("range_min"),scaling.get("range_max")
            ensure(isinstance(lo,(float,int)) and isinstance(hi,(float,int)) and lo<hi,"NUMERIC_SCALE_MISSING")
            if grid is None:
                count=int(scaling.get("inbound_outcome_count") or question.get("inbound_outcome_count") or 200)
                zero=scaling.get("zero_point")
                ratio=(hi-zero)/(lo-zero) if zero is not None else None
                grid=[lo+(hi-lo)*((ratio**(i/count)-1)/(ratio-1) if ratio is not None else i/count) for i in range(count+1)]
                grid[0],grid[-1]=lo,hi
            title=question.get("title") or post.get("title","")
            unit=question.get("unit")
            if not unit:
                unit=next((name for name in ("millions of dollars","billions of dollars","percent","percentage points") if name in title.lower()),"question-defined units")
            if kind=="date":
                convert=lambda x:datetime.fromtimestamp(x,timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")
                lo,hi=convert(lo),convert(hi);grid=[convert(x) for x in grid];unit="UTC"
            scale=dict(unit=unit,lower=lo,upper=hi,grid=grid,open_lower=bool(scaling.get("open_lower_bound",question.get("open_lower_bound"))),
                       open_upper=bool(scaling.get("open_upper_bound",question.get("open_upper_bound"))),
                       platform_scaling={k:v for k,v in scaling.items() if k not in {"range_min","range_max","nominal_min","nominal_max","open_lower_bound","open_upper_bound","inbound_outcome_count"}})
        options=[]
        for i,item in enumerate(question.get("options") or []):
            options.append(dict(id=str(item.get("id",i)),name=item.get("name") or item.get("label") or str(i)) if isinstance(item,dict) else dict(id=str(i),name=item))
        urls=rule_urls(rules)
        results.append(dict(question_id="metaculus:"+str(question["id"]),question_url=f"https://www.metaculus.com/questions/{post['id']}/",post_id=str(post["id"]),
            parent_event_id=None,created_at=post.get("created_at"),status="open" if post.get("status")=="open" else "unknown",
            definition=dict(title=question.get("title") or post.get("title"),description=question.get("description") or post.get("description") or "",
                resolution_criteria=rules,question_type=kind,options=options,scale=scale,condition=None,resolution_source_urls=urls,instrument_mapping=None,
                close_at=question.get("actual_close_time") or question.get("scheduled_close_time"),expected_resolution_at=question.get("scheduled_resolve_time"))))
    return results


def polymarket(market):
    outcomes=_array(market.get("outcomes"));tokens=_array(market.get("clobTokenIds"))
    ensure(len(outcomes)>=2 and len(tokens)==len(outcomes),"MARKET_TOKEN_MAPPING_MISSING")
    binary={str(x).lower() for x in outcomes}=={"yes","no"}
    options=[dict(id=str(i),name=name) for i,name in enumerate(outcomes)]
    rules=market.get("description");ensure(isinstance(rules,str) and rules.strip(),"MISSING_RESOLUTION_RULES")
    mapping={option["id"]:dict(token_id=str(token),outcome=option["name"]) for option,token in zip(options,tokens)}
    return dict(question_id="polymarket:"+str(market["id"]),question_url="https://polymarket.com/market/"+market["slug"],
        parent_event_id=str(market["events"][0]["id"]) if market.get("events") else None,post_id=None,created_at=market.get("createdAt"),
        status="open" if market.get("acceptingOrders") else "closed" if market.get("closed") else "unknown",
        definition=dict(title=market["question"],description="",resolution_criteria=rules,question_type="binary" if binary else "categorical",options=options,
            scale={},condition=None,resolution_source_urls=rule_urls(rules),
            instrument_mapping=mapping,close_at=market.get("endDate"),expected_resolution_at=None))
