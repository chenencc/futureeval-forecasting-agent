"""Observable document form and identity anchors, never truth adjudication."""
import re
from urllib.parse import unquote, urlsplit


def normalized(text):
    return re.sub(r'[^a-z0-9]+', ' ', unquote(text).lower()).strip()


def anchors(request):
    # Extract explicit identifiers; background prose is not a matching contract.
    text=' '.join(str(request.get(k,'')) for k in ('question','title','resolution_criteria'))
    found=re.findall(r'\b(?:SB|AB|HB|HR|H\.R\.)\s*\d{1,5}\b|\^[A-Z][A-Z0-9.]{1,10}\b|\b\d{2}-\d{3,6}\b',text)
    found+=re.findall(r'"([^"\n]{3,80})"',text)
    return sorted({normalized(s) for s in found if normalized(s)})


def identity(request,url,text=''):
    expected=anchors(request)
    observed=normalized(url+' '+text[:6000])
    matched=[a for a in expected if re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)',observed)]
    return {'explicit_anchors':expected,'matched_anchors':matched,
        'state':'anchor_observed' if matched else 'unmatched_candidate' if expected else 'not_assessed',
        'scope':'Literal acquisition routing only. No matched event, authority or truth is verified.'}


def page_form(url,page):
    body=page.get('content','').strip()
    if page.get('rows'):
        return {'state':'structured_data','body_coverage':'rows_saved'}
    path=urlsplit(url).path.rstrip('/').lower()
    # A short index with no dated entries is a possible shell, not empty evidence.
    index_path=bool(re.search(r'(?:/index(?:\.[a-z]+)?|/situations|/news|/disease-outbreak-news|/archive)$',path))
    dates=bool(re.search(r'\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d{2}\b',body,re.I))
    shell=index_path and len(body)<1200 and not dates and not re.search(r'^\s*\|.+\|',body,re.M)
    return {'state':'possible_index_shell' if shell else 'document_candidate',
        'body_coverage':'entries_not_observed' if shell else 'not_verified',
        'scope':'Conservative page-form diagnostic. Short announcements are not rejected by length alone.'}


def discovery_score(request,url,title,primary=False):
    match=identity(request,url,title)
    terms=set(re.findall(r'[a-z0-9]{4,}',str(request.get('question','')).lower()))-{'will','before','after','2026','question'}
    overlap=len(terms & set(re.findall(r'[a-z0-9]{4,}',unquote(url+' '+title).lower())))
    # A host bonus cannot outrank an explicit identifier in a candidate title.
    score=20*len(match['matched_anchors'])+overlap+int(bool(primary))
    if match['explicit_anchors'] and not match['matched_anchors'] and urlsplit(url).path.lower().endswith('.pdf'):
        score-=8
    return score
