"""Conservative text-table geometry and same-cell evidence binding.

No network, model, source-specific URL rules, missing-value filling or unit guessing.
Every header/cell/context reference retains its exact saved-text span and hash.
"""
import csv
import hashlib
import re
from decimal import Decimal, InvalidOperation

POLICY = 'literal-table-relations/1'
MAX_ROWS = 20000
MAX_COLUMNS = 32
MAX_TABLES = 64
MAX_BODY = 2000000
MISSING = {'', '-', '—', '–', 'n/a', 'na', 'null', '..', '...'}
DECIMAL = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+'
NUMBER = re.compile(r'(?P<currency>[$€£¥])?\s*(?P<number>[+\-−]?(?:'+DECIMAL+r')|\((?:'+DECIMAL+r')\))\s*(?P<percent>%)?')
TOKEN = r'(?:[$€£¥]\s*)?(?:[+\-−]?(?:'+DECIMAL+r')|\((?:'+DECIMAL+r')\)|[—–-])(?:\s*%)?'
QUARTER = re.compile(r'\bQ[1-4]\s+(?:19|20)\d\d\b', re.I)
DIMENSION = re.compile(r'^(?:yr|year|date|time|period|quarter|month|season|seas|id|code|unit|units)$', re.I)


def sha(text): return hashlib.sha256(text.encode('utf-8')).hexdigest()


def number(text):
    """Locale-conservative Decimal; preserve literal signs/symbols, never scale."""
    if text.strip().lower() in MISSING: return dict(status='missing', decimal=None)
    m = NUMBER.fullmatch(text.strip())
    if not m: return dict(status='not_numeric', decimal=None)
    raw = m['number']; negative = raw.startswith('(')
    try:
        value = Decimal(raw.strip('()').replace(',', '').replace('−', '-'))
        if negative: value = -value
    except InvalidOperation: return dict(status='not_numeric', decimal=None)
    return dict(status='numeric', decimal=str(value), symbol=m['currency'], percent=bool(m['percent']))


def lines(text):
    offset = 0; result = []
    for line in text.splitlines(keepends=True):
        raw = line.rstrip('\r\n'); result.append((offset, offset+len(raw), raw)); offset += len(line)
    return result


def ref(text, start, end, *, value=None):
    while start < end and text[start].isspace(): start += 1
    while end > start and text[end-1].isspace(): end -= 1
    raw = text[start:end]
    return dict(start=start, end=end, raw=raw, value=raw if value is None else value)


def split_cells(text, line, layout):
    a, b, s = line
    if layout == 'pipe':
        edges = [-1]+[m.start() for m in re.finditer(r'(?<!\\)\|', s)]+[len(s)]
        parts = [(x+1,y) for x,y in zip(edges,edges[1:])]
        if s.lstrip().startswith('|'): parts = parts[1:]
        if s.rstrip().endswith('|'): parts = parts[:-1]
        return [ref(text,a+x,a+y,value=s[x:y].strip().replace('\\|','|')) for x,y in parts]
    if layout == 'tsv':
        edges = [-1]+[m.start() for m in re.finditer('\t',s)]+[len(s)]
        return [ref(text,a+x+1,a+y) for x,y in zip(edges,edges[1:])]
    if layout == 'csv':
        if len(s) > 20000: return None
        # A physical line only; multiline CSV is explicitly unsupported.
        pattern = re.compile(r'(?:"(?:[^"]|"")*"|[^",]*)(?:,|$)')
        found = list(pattern.finditer(s))
        if not found or ''.join(m.group() for m in found) != s: return None
        result=[]
        for m in found:
            if m.start()==len(s): continue
            raw=m.group(); end=m.end()-(1 if raw.endswith(',') else 0)
            try: parsed=next(csv.reader([s[m.start():end]],strict=True))
            except (csv.Error,StopIteration):return None
            result.append(ref(text,a+m.start(),a+end,value=parsed[0] if parsed else ''))
        if s.endswith(','):result.append(ref(text,b,b,value=''))
        return result
    return [ref(text,a+m.start(),a+m.end()) for m in re.finditer(r'\S+',s)]


def period_tokens(text):
    """Explicit lexical period components; no year/season inferred from the question."""
    s=text.upper();out=set(re.findall(r'\b(?:19|20)\d\d\b',s))
    out.update(re.findall(r'\bQ[1-4]\b',s))
    for ordinal,q in [('FIRST','Q1'),('SECOND','Q2'),('THIRD','Q3'),('FOURTH','Q4')]:
        if re.search(r'\b'+ordinal+r'\s+QUARTER\b',s):out.add(q)
    out.update(re.findall(r'\b(?:DJF|JFM|FMA|MAM|AMJ|MJJ|JJA|JAS|ASO|SON|OND|NDJ)\b',s))
    months=('JAN(?:UARY)?','FEB(?:RUARY)?','MAR(?:CH)?','APR(?:IL)?','MAY','JUN(?:E)?',
            'JUL(?:Y)?','AUG(?:UST)?','SEP(?:TEMBER)?','OCT(?:OBER)?','NOV(?:EMBER)?','DEC(?:EMBER)?')
    for month,pattern in enumerate(months,1):
        if re.search(r'\b'+pattern+r'\b',s):out.add('MONTH:'+str(month))
    # Day and time components must not collapse to a matching year alone.
    for date in re.findall(r'(?<!\d)(?:19|20)\d\d[-/]\d{1,2}[-/]\d{1,2}(?!\d)',s):
        parts=re.split('[-/]',date);out.add('DATE:'+parts[0]+'-'+parts[1].zfill(2)+'-'+parts[2].zfill(2))
    out.update('DATE_RAW:'+d for d in re.findall(r'\b\d{1,2}[-/]\d{1,2}[-/](?:19|20)\d\d\b',s))
    out.update('DAY:'+d for d in re.findall(r'\b([0-3]?\d)(?=\s+(?:'+ '|'.join(months)+r')\b)',s))
    out.update('DAY:'+d for d in re.findall(r'\b(?:'+ '|'.join(months)+r')\s+([0-3]?\d)\b',s))
    out.update('TIME:'+v for v in re.findall(r'(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+\-]\d{2}:\d{2})?',s))
    out.update('FY:'+v for v in re.findall(r'\bFY\s*((?:19|20)\d\d)\b',s))
    if re.search(r'\bNINE\s+MONTHS\b',s):out.add('NINE_MONTHS')
    return out


def context_before(text, rows, index):
    context=[]
    # Tight boundary: no earlier tables, long prose or arbitrary heading inheritance.
    for j in range(index-1,max(-1,index-7),-1):
        a,b,s=rows[j];v=s.strip()
        if not v:continue
        if len(v)>250 or '|' in v or '\t' in v:break
        if QUARTER.fullmatch(v) or re.fullmatch(r'(?:19|20)\d\d',v):
            context.insert(0,{**ref(text,a,b),'role':'period'});break
        if re.search(r'\b(?:in (?:millions|thousands|billions)|units?:|amounts? in)\b',v,re.I):
            context.insert(0,{**ref(text,a,b),'role':'unit'})
        elif context:break
        else:break
    return context


def expand(text, parsed):
    """Expand only explicit spans from the existing HTML-to-text serializer."""
    grid=[];occupied={};width=0
    for rowno,cells in enumerate(parsed):
        row=dict(occupied.get(rowno,{}));col=0
        for cell in cells:
            while col in row:col+=1
            suffix=re.search(r'\s*\[(?:(?:rowspan|colspan)=\d+\s*)+\]$',cell['value'])
            spans=dict(re.findall(r'(rowspan|colspan)=(\d+)',suffix.group() if suffix else ''))
            rs,cs=int(spans.get('rowspan',1)),int(spans.get('colspan',1))
            if not 1<=rs<=MAX_ROWS or not 1<=cs<=MAX_COLUMNS or col+cs>MAX_COLUMNS or rowno+rs>len(parsed):
                raise ValueError('TABLE_INVALID_SPAN')
            v={**cell,'value':cell['value'][:suffix.start()].rstrip() if suffix else cell['value'],
               'origin_row':rowno,'origin_column':col,'rowspan':rs,'colspan':cs}
            for rr in range(rowno,rowno+rs):
                for cc in range(col,col+cs):
                    if cc in occupied.setdefault(rr,{}):raise ValueError('TABLE_OVERLAPPING_SPAN')
                    occupied[rr][cc]=v
                    if rr==rowno:row[cc]=v
            col+=cs
        width=max(width,max(row,default=-1)+1);grid.append(row)
    if not 2<=width<=MAX_COLUMNS or any(set(row)!=set(range(width)) for row in grid):
        raise ValueError('TABLE_RAGGED_ROWS')
    return [[row[c] for c in range(width)] for row in grid]


def make_table(text, body_hash, layout, rows, first, last, parsed, header_count, context):
    grid=expand(text,parsed)
    if not 1<=header_count<len(grid):raise ValueError('TABLE_HEADER_UNRESOLVED')
    start,end=rows[first][0],rows[last][1]
    tid='t'+sha(body_hash+':'+str(start)+':'+str(end))[:16]
    headers=[]
    for col in range(len(grid[0])):
        seen=set();path=[]
        for row in grid[:header_count]:
            c=row[col];key=(c['start'],c['end'])
            if key not in seen and c['value']:path.append(c);seen.add(key)
        headers.append(path)
    data=[]
    for rn,row in enumerate(grid[header_count:],header_count):
        data.append([dict(c,id='c'+sha(tid+':'+str(rn)+':'+str(cn))[:20],row=rn-header_count,column=cn,numeric=number(c['value'])) for cn,c in enumerate(row)])
    measures=[]
    for c,header in enumerate(headers):
        label=' '.join(h['value'] for h in header)
        statuses=[row[c]['numeric']['status'] for row in data]
        if label.strip() and not DIMENSION.fullmatch(label.strip()) and 'numeric' in statuses and all(x in {'numeric','missing'} for x in statuses):measures.append(c)
    notes=[]
    for a,b,s in rows[last+1:last+7]:
        if not s.strip():
            if notes:break
            continue
        if re.match(r'^\s*(?:\([a-z0-9]+\)|\*{1,3}|notes?\s*:)',s,re.I):notes.append(ref(text,a,b))
        else:break
    return dict(id=tid,layout=layout,start=start,end=end,body_sha256=body_hash,
        headers=headers,rows=data,measure_columns=measures,context=context,
        notes=notes,
        status='structured',semantic_truth_verified=False)


def pdf_row(text,line,count):
    a,b,s=line
    # Exactly N numeric tokens at the line end, leaving a nonempty textual row label.
    matches=list(re.finditer(TOKEN,s));
    if len(matches)<count:return None
    tail=matches[-count:]
    if s[tail[-1].end():].strip() or any(s[x.end():y.start()].strip() for x,y in zip(tail,tail[1:])):return None
    prefix=s[:tail[0].start()].rstrip()
    if not prefix or not re.search(r'[A-Za-z]',prefix):return None
    if len(s[:tail[0].start()])-len(prefix)<1:return None
    return [ref(text,a,a+len(prefix))]+[ref(text,a+m.start(),a+m.end()) for m in tail]


def parse(text):
    body_hash=sha(text);result=dict(policy=POLICY,body_sha256=body_hash,tables=[],gaps=[],complete_scan=True)
    if len(text)>MAX_BODY:
        result.update(complete_scan=False);result['gaps'].append(dict(code='TABLE_BODY_LIMIT'));return result
    rows=lines(text);i=0;total=0
    def gap(code,start,end):result['gaps'].append(dict(code=code,start=start,end=end))
    while i<len(rows):
        if len(result['tables'])>=MAX_TABLES or total>=MAX_ROWS:
            result['complete_scan']=False;gap('TABLE_SCAN_LIMIT',rows[i][0],len(text));break
        a,b,s=rows[i];v=s.strip();layout=None;header_count=1;parsed=[];end=i
        if v.startswith('|') and v.endswith('|') and v.count('|')>=3:layout='pipe'
        elif '\t' in s:layout='tsv'
        elif ',' in s and s.count(',')>=1 and not re.search(r'[.!?]\s*$',s):layout='csv'
        quarters=list(QUARTER.finditer(s))
        if len(quarters)>=2 and not re.sub(QUARTER,'',s).strip():
            layout='pdf_numeric';header=[ref(text,a,a,value='')]+[ref(text,a+m.start(),a+m.end()) for m in quarters]
            parsed=[header];end=i+1
            while end<len(rows) and len(parsed)<MAX_ROWS-total+1:
                data=pdf_row(text,rows[end],len(quarters))
                if data is None:break
                parsed.append(data);end+=1
            if len(parsed)<3:i+=1;continue
        elif layout:
            # Parse a contiguous explicit-delimiter block. CSV needs >= 2 data rows.
            end=i
            while end<len(rows) and len(parsed)<MAX_ROWS-total+1:
                candidate=rows[end][2]
                belongs=(candidate.strip().startswith('|') and candidate.strip().endswith('|')) if layout=='pipe' else '\t' in candidate if layout=='tsv' else ',' in candidate
                if not candidate.strip() or not belongs:break
                cells=split_cells(text,rows[end],layout)
                if cells is None:gap('TABLE_QUOTING_UNRESOLVED',rows[end][0],rows[end][1]);break
                parsed.append(cells);end+=1
            if len(parsed)<(2 if layout=='pipe' else 3):i=max(i+1,end);continue
            separators=[k for k,r in enumerate(parsed) if all(re.fullmatch(r':?-{3,}:?',c['value']) for c in r)]
            if separators:
                if len(separators)!=1 or separators[0]<1:
                    gap('TABLE_HEADER_UNRESOLVED',a,rows[end-1][1]);i=end;continue
                header_count=separators[0];parsed.pop(separators[0])
            elif any('[colspan=' in c['value'] or ' colspan=' in c['value'] or '[rowspan=' in c['value'] for r in parsed[:2] for c in r):
                # Explicit merged header depth only when first-row rowspan declares it.
                spans=[int(n) for c in parsed[0] for n in re.findall(r'rowspan=(\d+)',c['value'])]
                if not spans:
                    gap('TABLE_HEADER_DEPTH_AMBIGUOUS',a,rows[end-1][1]);i=end;continue
                header_count=max(spans)
        else:
            # A simple header and homogeneous whitespace records, not general prose.
            cells=split_cells(text,rows[i],'whitespace')
            if 2<=len(cells)<=MAX_COLUMNS and all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9/%().-]*',c['value']) for c in cells):
                layout='whitespace';parsed=[cells];end=i+1
                while end<len(rows) and len(parsed)<MAX_ROWS-total+1:
                    data=split_cells(text,rows[end],layout)
                    if len(data)!=len(cells) or not any(number(c['value'])['status']=='numeric' for c in data):break
                    parsed.append(data);end+=1
                if len(parsed)<3:i+=1;continue
            else:
                # Flattened grouped headings lack cell coordinates. Preserve a gap.
                if len(re.findall(r'\b(?:19|20)\d\d\b',s))>=2 and '% Change' in s and re.fullmatch(r'[\d\s%Change]+',s):
                    end=i+1
                    while end<len(rows) and re.search(r'\d',rows[end][2]) and rows[end][2].strip():end+=1
                    gap('TABLE_FLATTENED_GROUP_HEADERS_AMBIGUOUS',a,rows[end-1][1]);i=end;continue
                if len(re.findall(TOKEN,s))>=2 and i+1<len(rows) and len(re.findall(TOKEN,rows[i+1][2]))>=2:
                    end=i+2
                    while end<len(rows) and rows[end][2].strip() and len(re.findall(TOKEN,rows[end][2]))>=2:end+=1
                    gap('TABLE_LIKE_LAYOUT_UNRESOLVED',a,rows[end-1][1]);i=end;continue
                i+=1;continue
        try:
            table=make_table(text,body_hash,layout,rows,i,end-1,parsed,header_count,context_before(text,rows,i))
            # Delimiter-like prose and CSV numbers alone are not reliable headers.
            if layout=='csv' and (not table['measure_columns'] or any(number(c['value'])['status']=='numeric' for c in parsed[0])):
                gap('TABLE_CSV_HEADER_UNRESOLVED',a,rows[end-1][1])
            else:
                result['tables'].append(table);total+=len(table['rows'])
                if not table['measure_columns'] and any(c['numeric']['status']=='numeric' for row in table['rows'] for c in row):
                    gap('TABLE_MEASUREMENT_HEADERS_UNRESOLVED',a,rows[end-1][1])
                if any(c['numeric']['status']=='numeric' and (c['rowspan']>1 or c['colspan']>1) for row in table['rows'] for c in row):
                    gap('TABLE_MERGED_VALUE_UNALLOCATED',a,rows[end-1][1])
        except ValueError as exc:gap(str(exc),a,rows[end-1][1])
        i=max(i+1,end)
    return result


def records(index):
    """One numeric cell with its own row dimensions and column header path."""
    for table in index['tables']:
        for row in table['rows']:
            for col in table['measure_columns']:
                cell=row[col]
                if cell['numeric']['status']!='numeric' or cell['colspan']>1 or cell['rowspan']>1:continue
                dims=[c for n,c in enumerate(row) if n not in table['measure_columns']]
                yield dict(cell_id=cell['id'],table_id=table['id'],row=cell['row'],column=col,
                    value=cell,headers=table['headers'][col],dimensions=dims,
                    dimension_headers=[table['headers'][c['column']] for c in dims],
                    notes=table['notes'],
                    context=table['context'],body_sha256=index['body_sha256'])


def period_for(record):
    texts=[c['value'] for c in record['headers']+record['dimensions']]
    texts += [c['value'] for c in record['context'] if c['role']=='period']
    return period_tokens(' '.join(texts))


def unit_for(record):
    value=record['value'];literal=value['numeric'];candidates=[];units=[]
    if literal.get('percent'):return dict(status='explicit',kind='percent',literal='%',scale=None,currency=None)
    for h in record['headers']+record['context']:
        v=h['value']
        if re.search(r'\b(?:except|excluding|unless|per share|ARPU)\b',v,re.I):
            return dict(status='ambiguous',literal=v,scale=None,currency=None)
        m=re.search(r'\b(thousands|millions|billions)\b',v,re.I)
        if m:candidates.append((v,m[1].lower()))
        # A short explicit parenthesized unit or Unit: declaration. No automatic
        # translation from a metric name to an implied physical unit.
        annotation=re.search(r'[([]\s*(°[CF]|mm|cm|km|kg|USD|EUR|GBP|JPY|m|g|s|ms)\s*[)\]]',v)
        if annotation:units.append(annotation[1])
        declared=re.fullmatch(r'\s*units?\s*:\s*([^\s]{1,24})\s*',v,re.I)
        if declared:units.append(declared[1])
    for cell,headers in zip(record['dimensions'],record['dimension_headers']):
        if any(h['value'].strip().lower() in {'unit','units'} for h in headers):units.append(cell['value'])
    if len(set(units))>1:return dict(status='ambiguous',literal=' | '.join(units),scale=None,currency=None)
    if len({x[1] for x in candidates})>1:return dict(status='ambiguous',literal=' | '.join(x[0] for x in candidates),scale=None,currency=None)
    # A currency symbol is preserved; '$' is deliberately not converted to USD.
    parts=([literal['symbol']] if literal.get('symbol') else [])+units+([candidates[0][0]] if candidates else [])
    return dict(status='explicit' if parts else 'unknown',literal=' '.join(parts),
        symbol=literal.get('symbol'),unit=units[0] if units else None,
        scale=candidates[0][1] if candidates else None,currency=None)


def compact(record):
    return dict(cell_id=record['cell_id'],table_id=record['table_id'],row=record['row'],column=record['column'],
        value_literal=record['value']['value'],decimal=record['value']['numeric']['decimal'],
        column_headers=[x['value'] for x in record['headers']],row_dimensions=[x['value'] for x in record['dimensions']],
        period_components=sorted(period_for(record)),unit=unit_for(record))


def choices(index,question,limit=12):
    target=period_tokens(question.get('question',''));words=set(re.findall(r'[a-z]{4,}',question.get('question','').lower()))
    def score(record):
        labels=' '.join(c['value'] for c in record['headers']+record['dimensions']).lower()
        periods=period_for(record)
        return (len(target & periods)*5+sum(w in labels for w in words)*3,
                max((int(p) for p in periods if p.isdecimal() and len(p)==4),default=0),record['value']['start'])
    ordered=sorted(records(index),key=score,reverse=True)
    return dict(policy=POLICY,cells=[compact(r) for r in ordered[:limit]],omitted_numeric_cells=max(0,len(ordered)-limit),
                layout_gaps=index['gaps'],complete_scan=index['complete_scan'])


def bind(index, *, value, period, metric=None, unit=None, cell_id=None, span=None):
    """Bind value AND period AND metric within one row/column, not across a page."""
    numeric=number(value or '')
    if numeric['status']!='numeric':
        table_scope=bool(cell_id or span and any(t['start']<span['end'] and span['start']<t['end'] for t in index['tables']))
        return dict(status='value_unresolved' if table_scope else 'not_numeric',policy=POLICY)
    relevant=[r for r in records(index) if (r['cell_id']==cell_id if cell_id else span is None or
              span['start']<=r['value']['start'] and r['value']['end']<=span['end'])]
    if not relevant:
        overlapping=bool(span and any(t['start']<span['end'] and span['start']<t['end'] for t in index['tables']))
        gap_overlap=bool(span and any(g.get('start',0)<span['end'] and span['start']<g.get('end',0) for g in index['gaps']))
        return dict(status='unresolved_layout' if gap_overlap else 'cell_not_found' if cell_id or overlapping else 'not_table',policy=POLICY)
    values=[r for r in relevant if Decimal(r['value']['numeric']['decimal'])==Decimal(numeric['decimal'])]
    if not values:return dict(status='value_mismatch',policy=POLICY)
    requested=period_tokens(period or '')
    if not requested:return dict(status='period_unresolved',policy=POLICY)
    matching=[r for r in values if requested==period_for(r)]
    if not matching:return dict(status='period_mismatch',policy=POLICY,
        observed_periods=[sorted(period_for(r)) for r in values[:8]])
    if metric:
        normalized=lambda s:' '.join(s.lower().split())
        matching=[r for r in matching if normalized(metric) in {normalized(c['value']) for c in r['headers']+r['dimensions']}]
        if not matching:return dict(status='metric_mismatch',policy=POLICY)
    else:return dict(status='metric_unresolved',policy=POLICY)
    if len(matching)!=1:return dict(status='ambiguous_cell',policy=POLICY)
    selected=matching[0]
    observed_unit=unit_for(selected)
    if observed_unit['status']!='explicit':unit_status='unresolved'
    elif not unit:unit_status='not_asserted'
    else:
        words=lambda s:set(re.findall(r'[A-Za-z°%$€£¥]+',s.lower()))
        wanted=words(unit);available=words(observed_unit['literal'])
        if observed_unit.get('kind')=='percent' and unit.lower() in {'percent','percentage','%'}:unit_status='match'
        elif wanted and wanted<=available and (not observed_unit.get('scale') or observed_unit['scale'] in wanted):unit_status='match'
        else:unit_status='mismatch'
    return dict(status='unit_mismatch' if unit_status=='mismatch' else 'bound',policy=POLICY,record=compact(selected),unit_status=unit_status,
        source_refs=dict(value=selected['value'],headers=selected['headers'],dimensions=selected['dimensions'],context=selected['context'],notes=selected['notes']),
        body_sha256=index['body_sha256'],truth_verified=False)
