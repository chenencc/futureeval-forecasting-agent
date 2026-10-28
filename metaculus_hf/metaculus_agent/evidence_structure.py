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

def units(question,text,rows):
    """Return exact slices and dependency slices for recognizable data layouts."""
    result=[]
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
