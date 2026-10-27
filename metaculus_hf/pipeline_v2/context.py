"""Deterministic arm-scoped delivery; full clean text remains in the snapshot.

Select complete text blocks and complete tables, never silently slice a table.
The original arm may read explicitly unverified pending bodies; the graph arm
receives only reviewed bindings. Block selection and span integrity are shared.
"""
from copy import deepcopy
import re
from .contracts import canonical,PipelineError
from .admission import decision_view

POLICY="arm-scoped-readable-blocks-54k-v4"


def _blocks(text):
    lines=list(re.finditer(r"[^\n]*(?:\n|$)",text))
    result=[];i=0
    while i<len(lines):
        item=lines[i]
        if not item.group().strip():i+=1;continue
        start=item.start();table=item.group().lstrip().startswith("|")
        j=i+1
        if table:
            while j<len(lines) and lines[j].group().lstrip().startswith("|"):j+=1
            # Caption/units/period context is retained with the complete table.
            before=i
            while before>max(0,i-2) and len(lines[before-1].group())<=1600:before-=1
            start=lines[before].start();end=lines[j-1].end()
            after=j
            while after<min(len(lines),j+2) and len(lines[after].group())<=1600:
                end=lines[after].end();after+=1
        else:
            end=item.end()
            if end-start>1600:
                # Long non-table lines become exact sentence/word spans, never
                # a sliced table. Adjacent selected spans merge back losslessly.
                cursor=start
                for token in re.finditer(r"\S+\s*",text[start:end]):
                    stop=start+token.end()
                    if stop-cursor>=1200:result.append((cursor,stop,False));cursor=stop
                if cursor<end:result.append((cursor,end,False))
                i=j;continue
        result.append((start,end,table));i=j
    return result


def delivery(snapshot,limit=54000,*,arm='official_graph'):
    view=decision_view(snapshot,arm)
    terms=set(re.findall(r"[a-z0-9]{4,}",(snapshot["definition"]["title"]+" "+snapshot["definition"]["resolution_criteria"]).lower()))
    readable=[m for m in view["materials"] if m["text"]]
    candidates=[];selected={};omissions=[]
    for index,m in enumerate(readable):
        text=m["text"];selected[index]=[]
        for start,end,table in _blocks(text):
            segment=text[start:end]
            score=sum(len(t) for t in terms if t in segment.lower())+ (12 if table else 0)+(10 if start==0 else 0)
            candidates.append((score,index,start,end,table,len(segment.encode("utf-8"))))
    remaining=limit
    for _,index,start,end,table,size in sorted(candidates,key=lambda x:(-x[0],x[1],x[2])):
        if any(start>=a and end<=b for a,b in selected[index]):continue
        if size>remaining:
            if table:omissions.append(dict(source_id=readable[index]["source"]["source_id"],code="TABLE_NOT_DELIVERED_CONTEXT_LIMIT"))
            continue
        selected[index].append((start,end));remaining-=size
    for index,m in enumerate(readable):
        intervals=[]
        for start,end in sorted(selected[index]):
            if intervals and start<=intervals[-1][1]:intervals[-1]=(intervals[-1][0],max(end,intervals[-1][1]))
            else:intervals.append((start,end))
        original=m["text"]
        spans=[];parts=[];offset=0;separator="\n[... omitted; not delivered ...]\n"
        for a,b in intervals:
            if parts:offset+=len(separator)
            piece=original[a:b];spans.append(dict(start=a,end=b,view_start=offset,view_end=offset+len(piece)));parts.append(piece);offset+=len(piece)
        m["delivery_spans"]=spans
        m["text"]=separator.join(parts)
        if len(intervals)!=1 or (intervals and intervals[0]!=(0,len(original))):
            omissions.append(dict(source_id=m["source"]["source_id"],code="SOURCE_PARTIALLY_DELIVERED",original_characters=len(original),delivered_characters=sum(b-a for a,b in intervals)))
    view["delivery"]=dict(policy=POLICY,omissions=omissions,instruction="Only delivery_spans were read. Omitted text is unknown to this call; do not infer its contents. A partial historical/calendar page is not proof that a future event is absent.")
    # One derived view, not a second persisted snapshot. Offsets refer to exact saved text.
    return view


def fit_delivery(snapshot,render,limit=64000,*,arm='official_graph',measure=None,allow_empty=False):
    """Budget the exact final JSON envelope, including rules, gaps and metadata."""
    from .repository import ensure
    ensure(not snapshot.get('delivery'),'PACKING_REQUIRES_FULL_SNAPSHOT')
    body_limit=54000
    measure=measure or (lambda p:len(canonical(p).encode('utf-8')))
    for _ in range(12):
        view=delivery(snapshot,body_limit,arm=arm);payload=render(view)
        size=max(measure(p) for p in payload)
        if size<=limit:
            ensure(any(m['text'] for m in view['materials']) or allow_empty,'EVIDENCE_BLOCK_EXCEEDS_CONTEXT')
            return view
        if body_limit==0:raise PipelineError('IMMUTABLE_CONTEXT_TOO_LARGE')
        # Wire escaping can expand non-ASCII much faster than source UTF-8.
        # Never subtract that expansion into an immediate zero-body budget.
        body_limit=max(0,min(body_limit-1024,max(body_limit//2,body_limit-(size-limit)-512)))
    raise PipelineError('IMMUTABLE_CONTEXT_TOO_LARGE')
