"""Literal structures for compact evidence, never synthesized facts or counts."""
import re,unicodedata
from urllib.parse import urlsplit

def words(text):
    text=''.join(c for c in unicodedata.normalize('NFKD',str(text)) if not unicodedata.combining(c)).lower()
    return set(re.findall(r'[a-z0-9]+',text))

def primary(question,url):
    refs=re.findall(r'https?://[^\s<>\)]+',question.get('resolution_criteria','')+' '+question.get('fine_print',''))
    clean=lambda value:value.rstrip('/.,;')
    return any(clean(url)==clean(ref) for ref in refs)

def navigation_fraction(text):
    rows=[r.strip() for r in text.splitlines() if r.strip()]
    if not rows:return 1.
    nav=re.compile(r'^(?:skip to|menu$|search(?: dialog)?$|home$|close$|back$|select language|english$|deutsch$|español|français|italiano|日本語|简体中文|share$|email$|print$|facebook$|twitter$|linkedin$|whatsapp$|wechat$|xing$|line$|global site$|your current region|cookie|sign in|log in|subscribe)',re.I)
    return sum(bool(nav.search(row)) for row in rows)/len(rows)

def quarterly_focus(question,text):
    """A weak relevance cue for quarterly measurements, never factual admission."""
    title=question.get('question','')
    if not re.search(r'\bQ[1-4]\s+20\d\d\b',title,re.I):return None
    stems=('deliver','revenu','advertis','earning','profit','product','sales','subscriber','user')
    wanted=[stem for stem in stems if any(w.startswith(stem) for w in words(title))]
    if not wanted:return None
    return sum(any(w.startswith(stem) for w in words(text)) for stem in wanted)

def exact_quarter_metric(question,text):
    title=question.get('question','')
    if not re.search(r'\bQ[1-4]\s+20\d\d\b',title,re.I):return False
    if re.search(r'digital\s+advertising\s+revenu',title,re.I):
        return bool(re.search(r'digital\s+advertising\s+revenu',text,re.I))
    if re.search(r'vehicles?.*deliver|deliver.*vehicles?',title,re.I):
        return bool(re.search(r'\bdeliver(?:ies|ed|y)\b',text,re.I))
    return False

def old_quarterly_context(question,text):
    target=re.search(r'\bQ[1-4]\s+(20\d\d)\b',question.get('question',''),re.I)
    years=[int(y) for y in re.findall(r'\b(?:19|20)\d\d\b',text)]
    return bool(target and years and max(years)<int(target[1])-2)

def units(question,text,rows):
    """Return exact slices and dependency slices for recognizable data layouts."""
    result=[]
    # Short metric paragraphs retain literal numbers, units and nearby period
    # context. These selection cues do not approve the claims or infer values.
    if exact_quarter_metric(question,text):
        context_end=max((b for a,b,line in rows if b<=400),default=0)
        context=[(0,context_end)] if context_end and re.search(r'20\d\d',text[:context_end]) and re.search(r'quarter|Q[1-4]',text[:context_end],re.I) else []
        for i,(a,b,line) in enumerate(rows):
            if not context or line.lstrip().startswith('|'):continue
            # Wrapped prose may split the metric over adjacent PDF lines.
            end=min(i+4,len(rows));window=text[a:rows[end-1][1]]
            if not exact_quarter_metric(question,window):continue
            # Keep prose through a complete sentence; avoid flat financial rows
            # whose numeric columns need their own multi-level table headers.
            stop=next((j for j in range(i,end) if re.search(r'[.!?]\s*$',rows[j][2]) and re.search(r'\d',text[a:rows[j][1]]) and exact_quarter_metric(question,text[a:rows[j][1]])),None)
            if stop is None or rows[stop][1]-a>800 or re.search(r'\$[^\n]*\$[^\n]*\$',window):continue
            if a<400:continue
            start=i
            if i and rows[i-1][2].strip() and len(rows[i-1][2].strip())<65 and not re.search(r'[.!?:]\s*$',rows[i-1][2]) and not re.search(r'\d',rows[i-1][2]):start=i-1
            if any(x['start']<=rows[start][0]<x['end'] for x in result):continue
            result.append(dict(start=rows[start][0],end=rows[stop][1],kind='metric_paragraph',priority=0,deps=context,group='literal_metric_paragraphs',rows=1))
    # Fixed-width season/year/value series: retain labels and current rows as a
    # single complete window. No target row/value is fabricated when absent.
    series=[(i,a,b,int(m[2])) for i,(a,b,line) in enumerate(rows) if (m:=re.fullmatch(r'\s*([A-Z]{2,4})\s+(19\d\d|20\d\d)\s+[-+]?\d+(?:\.\d+)?\s*',line))]
    if len(series)>=5:
        header=next(((a,b) for a,b,line in rows[:8] if re.search(r'\b(?:YR|YEAR)\b',line) and re.search(r'\b(?:ANOM|VALUE|INDEX)\b',line)),None)
        if header:
            latest=max(x[3] for x in series)
            for year in [latest,latest-1]:
                matching=[x for x in series if x[3]==year]
                if matching:result.append(dict(start=matching[0][1],end=matching[-1][2],kind='series_window',priority=-2 if year==latest else 0,deps=[header],group='series_'+str(year),rows=len(matching)))
    # Repeated record labels keep their original event heading and winning
    # value, excluding unrelated runner-up positions. Each row remains literal.
    if re.search(r'\b(?:win|wins|winner|winners)\b',question.get('question',''),re.I):
        for i,(a,b,line) in enumerate(rows):
            if not re.fullmatch(r'\s*Winner:\s*',line,re.I):continue
            if i+1>=len(rows):continue
            heading=i-1
            while heading>=0 and not rows[heading][2].strip():heading-=1
            if heading<0 or a-rows[heading][0]>300:continue
            if not re.search(r'\b(?:round|prix|race|match|tournament|event)\b',rows[heading][2],re.I):continue
            result.append(dict(start=rows[heading][0],end=rows[i+1][1],kind='record_fact',priority=-2,deps=[],group='winner_records',rows=1))
    return result
