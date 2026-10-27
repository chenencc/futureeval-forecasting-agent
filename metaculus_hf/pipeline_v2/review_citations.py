"""Body-specific literal citation choices; selection is not evidence truth certification."""
import hashlib
import re
from copy import deepcopy
from .retrieval_plan import folded

POLICY='body-bound-citation-id-v1'


def citation_choices(source_id,text,definition,*,limit=24):
    """Offer bounded overlapping verbatim passages; never replace the full saved body."""
    if not isinstance(text,str) or not text.strip():return []
    terms=set(re.findall(r'[a-z0-9]{4,}',folded(definition.get('title',''))))-{'will','would','before','after','this','that','does'}
    body_hash=hashlib.sha256(text.encode()).hexdigest();candidates=[]
    start=0
    while start<len(text):
        end=min(len(text),start+1000)
        if end<len(text):
            newline=text.rfind('\n',start+500,end)
            if newline>start:end=newline
        if end-start>=12:
            quote=text[start:end];score=sum(t in folded(quote) for t in terms)
            candidates.append((score,start,end))
        if end==len(text):break
        start=max(start+1,end-120)
    picked=sorted(sorted(candidates,key=lambda row:(-row[0],row[1]))[:limit],key=lambda row:row[1])
    return [dict(citation_index=index,citation_id='c'+hashlib.sha256((source_id+'\0'+body_hash+'\0'+str(start)+':'+str(end)).encode()).hexdigest()[:20],
        source_id=source_id,body_sha256=body_hash,span=dict(start=start,end=end),quote=text[start:end]) for index,(_,start,end) in enumerate(picked,1)]


def bind_review_citation(proposal,material):
    """Resolve only an ID supplied for this exact source/body; never fuzzy-match a claim."""
    result=deepcopy(proposal);review=result.get('relevance')
    if not isinstance(review,dict) or not ({'citation_id','citation_index'} & set(review)):return result,None
    choices=material.get('citation_choices') or []
    if 'citation_index' in review:
        index=review.pop('citation_index')
        if index is None:
            if review.get('citation_id') is not None:return result,'REVIEW_CITATION_CONFLICT'
            review.pop('citation_id',None);review.pop('quote',None);return result,None
        selected=next((c for c in choices if type(index)==int and c.get('citation_index')==index),None)
        if selected is None:return result,'REVIEW_CITATION_UNBOUND'
        if 'citation_id' in review and review['citation_id']!=selected['citation_id']:return result,'REVIEW_CITATION_CONFLICT'
        review['citation_id']=selected['citation_id']
    ident=review.pop('citation_id')
    if ident is None:
        review.pop('quote',None);return result,None
    selected=next((c for c in choices if c['citation_id']==ident),None)
    if not selected or proposal.get('source_id')!=material['source_id']:return result,'REVIEW_CITATION_UNBOUND'
    text=material['text'];span=selected['span']
    if selected['source_id']!=material['source_id'] or hashlib.sha256(text.encode()).hexdigest()!=selected['body_sha256']:
        return result,'REVIEW_CITATION_BODY_CHANGED'
    if text[span['start']:span['end']]!=selected['quote']:return result,'REVIEW_CITATION_BODY_CHANGED'
    review['quote']=selected['quote']
    review['span']=deepcopy(span)
    return result,None
