"""Observable document form and identity anchors, never truth adjudication."""
import re
import calendar
from datetime import date, timedelta
from urllib.parse import unquote, urlsplit
from ForecastAgent.readers.quality import navigation_shell

MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
MONTH_PATTERN = '|'.join(MONTHS)


def target_period(request):
    """Extract explicit calendar routing hints; never infer historical availability."""
    question = str(request.get('question', request.get('title', '')))
    months = re.findall(r'\b('+MONTH_PATTERN+r')\s+(20\d{2})\b', question, re.I)
    if len(set((m.lower(), y) for m, y in months)) == 1:
        month, year = months[0]; year = int(year); month = MONTHS[month.lower()]
        return {'start': date(year, month, 1).isoformat(),
                'end_exclusive': date(year+int(month == 12), month % 12+1, 1).isoformat(),
                'basis': 'explicit_question_month', 'year_hint': year}
    criteria = str(request.get('resolution_criteria', ''))
    boundaries = re.findall(r'\b(after|before)\s+('+MONTH_PATTERN+r')\s+(\d{1,2}),?\s+(20\d{2})\b', criteria or question, re.I)
    values = {}
    try:
        for role, month, day, year in boundaries:
            values.setdefault(role.lower(), set()).add(date(int(year), MONTHS[month.lower()], int(day)))
    except ValueError:
        return {'basis': 'ambiguous', 'year_hint': None}
    if any(len(dates) > 1 for dates in values.values()):
        return {'basis': 'ambiguous', 'year_hint': None}
    start = next(iter(values['after']))+timedelta(days=1) if 'after' in values else None
    end = next(iter(values['before'])) if 'before' in values else None
    if start and end and start >= end:
        return {'basis': 'ambiguous', 'year_hint': None}
    return {'start': start.isoformat() if start else None, 'end_exclusive': end.isoformat() if end else None,
            'year_hint': (end or start).year if end or start else None,
            'basis': 'explicit_day_boundaries' if values else 'not_observed'}


def period_priority(request, url, title):
    """Rank visible candidate dates only; retain every out-of-period source."""
    window = target_period(request)
    text = unquote(url+' '+title)
    dates = re.findall(r'\b((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b', text)
    dates += [(y, str(MONTHS[m.lower()]), d) for m, d, y in
              re.findall(r'\b('+MONTH_PATTERN+r')\s+(\d{1,2}),?\s+(20\d{2})\b', text, re.I)]
    observed = []
    for y, m, d in dates:
        try: observed.append(date(int(y), int(m), int(d)))
        except ValueError: continue
    start = date.fromisoformat(window['start']) if window.get('start') else None
    end = date.fromisoformat(window['end_exclusive']) if window.get('end_exclusive') else None
    if start and end and any(start <= value < end for value in observed): return 8
    if start and end and observed: return -3
    years = {int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', text)}
    year = window.get('year_hint')
    return 3 if year and year in years else -6 if year and years else 0


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
    shell=navigation_shell(body) or (index_path and len(body)<1200 and not dates and not re.search(r'^\s*\|.+\|',body,re.M))
    return {'state':'possible_index_shell' if shell else 'document_candidate',
        'body_coverage':'entries_not_observed' if shell else 'not_verified',
        'scope':'Conservative page-form diagnostic. Short announcements are not rejected by length alone.'}


def discovery_score(request,url,title,primary=False,published_date=None):
    match=identity(request,url,title)
    terms=set(re.findall(r'[a-z0-9]{4,}',str(request.get('question','')).lower()))-{'will','before','after','2026','question'}
    overlap=len(terms & set(re.findall(r'[a-z0-9]{4,}',unquote(url+' '+title).lower())))
    # A host bonus cannot outrank an explicit identifier in a candidate title.
    period_text=title+' '+str(published_date or '')
    period_score=max(-3,min(3,period_priority(request,url,period_text)))
    score=20*len(match['matched_anchors'])+3*overlap+int(bool(primary))+period_score
    if match['explicit_anchors'] and not match['matched_anchors'] and urlsplit(url).path.lower().endswith('.pdf'):
        score-=8
    return score
