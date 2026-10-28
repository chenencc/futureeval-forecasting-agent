"""Pack bound original passages and complete table rows, never generated summaries."""
from copy import deepcopy
import hashlib,json,re
from . import decision_model

POLICY='review-bound-original-packing/1'

class PackingError(ValueError):
    category='configuration'
    def __init__(self,code):self.code=code;super().__init__(code)

def measure(state,registry):
    return len(json.dumps(dict(model=decision_model.MODEL,state=state,questions=registry)).encode())

def lines(text):
    result=[];offset=0
    for line in text.splitlines(keepends=True):
        result.append((offset,offset+len(line),line));offset+=len(line)
    return result

def layout(text):
    """Identify literal Markdown table runs. Unknown layouts stay ordinary text."""
    rows=lines(text);tables=[];index=0
    while index<len(rows):
        if not rows[index][2].lstrip().startswith('|') or rows[index][2].count('|')<2:index+=1;continue
        end=index+1
        while end<len(rows) and rows[end][2].lstrip().startswith('|') and rows[end][2].count('|')>=2:end+=1
        if end-index>=2:
            header_end=index+1
            if header_end<end and re.fullmatch(r'[\s|:\-]+',rows[header_end][2]):header_end+=1
            tables.append(dict(start=rows[index][0],end=rows[end-1][1],header=(rows[index][0],rows[header_end-1][1]),
                rows=[(a,b) for a,b,_ in rows[header_end:end]],
                caption=(rows[index-1][0],rows[index-1][1]) if index and rows[index-1][2].strip() else None,
                note=(rows[end][0],rows[end][1]) if end<len(rows) and re.match(r'\s*(?:notes?\b|\*|the status\b)',rows[end][2],re.I) else None))
        index=end
    return rows,tables

def _span(source,text,a,b):
    return dict(evidence_id='E'+hashlib.sha256((source['source_id']+'\0'+str(a)+':'+str(b)).encode()).hexdigest()[:16],
        source_id=source['source_id'],url=source['url'],body_sha256=source['body_sha256'],start=a,end=b,text=text[a:b])

def prepare(packet,source):
    """Derive a reading library; immutable inputs and all source aliases survive."""
    packet=deepcopy(packet);groups={};kept=[];aliases=[];library=[];meta={}
    for item in packet['sources']:
        text=source['pages'][item['url']]['content'];hashed=hashlib.sha256(text.encode()).hexdigest()
        if hashed!=item['body_sha256']:raise ValueError('SAVED_BODY_HASH_MISMATCH')
        group=groups.get(hashed)
        if group:
            group['urls'].append(item['url']);group['bindings'].append(source.get('source_bindings',{}).get(item['url'],{}))
            group['observations'].append(dict(url=item['url'],capture_metadata=deepcopy(item.get('capture_metadata',{}))));continue
        group=dict(item=item,text=text,urls=[item['url']],bindings=[source.get('source_bindings',{}).get(item['url'],{})],
                   observations=[dict(url=item['url'],capture_metadata=deepcopy(item.get('capture_metadata',{})))]);groups[hashed]=group
    title=packet['question'].get('question','')
    terms=set(re.findall(r'[a-z0-9]{3,}',title.lower()))-set('the will what when where how many before after there have been does this that with from into which first announce announced achieve achieved happen occur would could should'.split())
    entities={x.lower() for x in re.findall(r'\b[A-Z][A-Z0-9]{2,}\b',title)}
    for hashed,group in groups.items():
        item=group['item'];text=group['text'];body_lines,tables=layout(text)
        # The full capture metadata remains in context and the saved packet audit.
        original=item.get('capture_metadata') or {}
        item['capture_metadata']={k:deepcopy(original[k]) for k in ('retrieved_at_utc','published_at','http_status','captured_at','extraction_version') if k in original}
        item['saved_body_characters']=len(text)
        narrative=sum(len(line) for _,_,line in body_lines if len(line.strip())>=160)/max(1,len(text))
        kept.append(item);aliases.append(dict(source_id=item['source_id'],body_sha256=hashed,urls=group['urls'],observations=group['observations'],exact_body_duplicate_count=len(group['urls'])-1))
        date_context=None
        def add(a,b,kind,priority=3,deps=()):
            if not 0<=a<b<=len(text):return None
            deps=list(deps)
            if date_context and not (a<=meta[date_context]['start'] and meta[date_context]['end']<=b):deps=[date_context]+deps
            span=_span(item,text,a,b);ident=span['evidence_id'];old=meta.get(ident)
            if old:
                if old['kind']=='document_header' and kind=='body':old['kind']='body'
                old['priority']=min(priority,old['priority']);old['dependencies']=list(dict.fromkeys(old['dependencies']+list(deps)));return ident
            library.append(span);words=set(re.findall(r'[a-z0-9]+',span['text'].lower()))
            meta[ident]=dict(kind=kind,priority=priority,dependencies=list(deps),relevance=sum(4 if t in entities else 1 for t in terms if t in words),
                source_id=item['source_id'],start=a,end=b,narrative_fraction=narrative)
            return ident
        # Visible document dates are original text, never inferred from fetch time.
        date_ends=[]
        date_pattern=r'\b(?:20\d\d[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[./]\d{1,2}[./]20\d\d|\d{1,2}\s+[A-Za-z]{3,9},?\s+20\d\d|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d\d)\b'
        for index,(a,b,line) in enumerate(body_lines):
            if a>300:break
            if b<=300 and len(line.strip())<=100 and re.search(date_pattern,line):date_ends.append(b)
            if re.search(r'last\s+update',line,re.I):
                date_end=b
                for _,end,following in body_lines[index+1:index+4]:
                    if len(following.strip())>90:break
                    date_end=end
                date_ends.append(date_end)
        if date_ends and max(date_ends)<=500:date_context=add(0,max(date_ends),'document_header')
        # Bound model reviews select original locations, not the reviewer's prose.
        review_ranges=[]
        for binding in group['bindings']:
            ref=(binding.get('relevance') or {}).get('span')
            if not ref or binding.get('inclusion') not in {'core','supporting'}:continue
            a,b=ref.get('start'),ref.get('end')
            if type(a)!=int or type(b)!=int or not 0<=a<b<=len(text):continue
            if not 12<=b-a<=1600:continue
            # Never cut a quoted table row; align only touched lines in tables.
            for table in tables:
                if a<table['end'] and b>table['start']:
                    for x,y,_ in body_lines:
                        if x<=a<y and table['start']<=x<table['end']:a=x
                        if x<b<=y and table['start']<=x<table['end']:b=y
            priority=0 if binding.get('forecast_relevance') in {'direct','baseline'} or binding.get('source_role')=='historical_baseline' else 2
            review_ranges.append((a,b,priority))
        table_ids=[]
        for table in tables:
            header=add(*table['header'],'table_header')
            caption=add(*table['caption'],'table_caption') if table['caption'] else None
            note=add(*table['note'],'table_note') if table['note'] else None
            deps=[x for x in [caption,header,note] if x]
            rows=[add(a,b,'table_row',deps=deps) for a,b in table['rows']]
            table_ids.append((table,header,rows,deps))
        for a,b,priority in review_ranges:
            deps=[]
            for table,header,row_ids,required in table_ids:
                if a<table['end'] and b>table['start']:
                    deps.extend(ident for ident in required if not (a<=meta[ident]['start'] and meta[ident]['end']<=b))
                    touched=[(x,y) for x,y in table['rows'] if a<y and b>x]
                    # Offer progressively shorter complete row windows when a long
                    # citation cannot fit; headers/notes remain atomic dependencies.
                    if touched:
                        for n in sorted({1,2,3,5,10,len(touched)},reverse=True):
                            if n>len(touched):continue
                            start=touched[0][0];end=touched[n-1][1];window_deps=required
                            if start==table['rows'][0][0]:
                                start=table['caption'][0] if table['caption'] else table['header'][0]
                                window_deps=[i for i in required if not (start<=meta[i]['start'] and meta[i]['end']<=end)]
                            window_id=add(start,end,'review_table_window',priority,window_deps)
                            meta[window_id]['review_table_rows']=len(touched)
            add(a,b,'review_bound',priority,deps)
        # Exclude table ranges from ordinary chunking: a row cannot be bisected.
        cursor=0
        for a,b in [(t['start'],t['end']) for t in tables]+[(len(text),len(text))]:
            stop=a
            while cursor<stop:
                end=min(cursor+850,stop)
                if end<stop:
                    boundary=text.rfind('\n',cursor+250,end)
                    if boundary>=0:end=boundary+1
                add(cursor,end,'body');cursor=end
            cursor=b
    packet['sources']=kept;packet['evidence']=library
    packet['evidence_packing']=dict(policy=POLICY,aliases=aliases,candidates=meta,
        semantic_selection='Existing bound collection-model review; no extra model calls or rewritten facts.',
        table_policy='Complete literal rows with original headers, captions and recognized notes; omitted rows remain omitted.')
    return packet

def select(packet,state,limit,registry,reasons=()):
    state=deepcopy(state);info=packet['evidence_packing'];meta=info['candidates'];by_id={e['evidence_id']:e for e in packet['evidence']}
    source_map={s['source_id']:{k:deepcopy(s[k]) for k in ('source_id','url','body_sha256','capture_metadata','saved_body_truncated','saved_body_characters') if k in s} for s in packet['sources']}
    if measure(state,registry)>limit:raise PackingError('DECISION_BASE_INPUT_TOO_LARGE')
    kept={e['evidence_id'] for e in state['evidence']};skipped=[]
    def covered(span,evidence):return any(x['source_id']==span['source_id'] and x['start']<=span['start'] and x['end']>=span['end'] for x in evidence)
    def overlap(span):return any(x['source_id']==span['source_id'] and max(x['start'],span['start'])<min(x['end'],span['end']) for x in state['evidence'])
    def offer(ident):
        if ident in kept or covered(by_id[ident],state['evidence']):return
        span=by_id[ident]
        # Later filler never crowds an already-selected bound passage with repeats.
        if meta[ident]['kind'] in {'body','review_bound','review_table_window'} and overlap(span):return
        pending=[]
        for dep in meta[ident]['dependencies']+[ident]:
            if dep not in kept and not covered(by_id[dep],state['evidence']+pending):pending.append(deepcopy(by_id[dep]))
        candidate=deepcopy(state)
        for entry in pending:
            candidate['evidence'].append(entry)
            if entry['source_id'] not in {s['source_id'] for s in candidate['sources']}:candidate['sources'].append(source_map[entry['source_id']])
        # context_omitted may change from true to false (one extra JSON byte).
        if measure(candidate,registry)+int(candidate.get('context_omitted') is True)<=limit:
            state.update(candidate);kept.update(x['evidence_id'] for x in pending)
        else:skipped.append(ident)
    # Strong model-bound baselines/direct facts precede navigation and lexical filler.
    for ident in sorted(meta,key=lambda x:(meta[x]['priority'],meta[x]['kind']!='review_table_window',-meta[x].get('review_table_rows',0),-meta[x]['relevance'],meta[x]['source_id'],meta[x]['start'],-meta[x]['end'])):
        if meta[ident]['priority']==0:offer(ident)
    # Fair ranked turns, rather than giving each source's header first priority.
    groups={}
    for ident in meta:
        if meta[ident]['kind'] in {'table_header','table_caption','table_note','document_header'}:continue
        groups.setdefault(meta[ident]['source_id'],[]).append(ident)
    for group in groups.values():group.sort(key=lambda x:(-meta[x]['relevance'],meta[x]['priority'],meta[x]['start']))
    ordered=sorted(groups.values(),key=lambda g:-meta[g[0]]['narrative_fraction'])
    for index in range(max((len(g) for g in groups.values()),default=0)):
        for group in ordered:
            if index<len(group):offer(group[index])
    if packet['evidence'] and not state['evidence']:raise PackingError('DECISION_EVIDENCE_BUDGET_EXHAUSTED')
    delivered=state['evidence'];review_coverage=[]
    state['context_omitted']=any(not covered(e,delivered) for e in packet['evidence'])
    for ident,m in meta.items():
        if m['kind']=='review_bound':review_coverage.append(dict(evidence_id=ident,complete=covered(by_id[ident],delivered)))
    return state,dict(policy=POLICY,selected_ids=sorted(kept),omitted_ids=[x for x in meta if x not in kept],
        request_bytes=measure(state,registry),request_byte_limit=limit,routing_focus=list(reasons),budget_omitted_ids=skipped,
        review_coverage=review_coverage,table_rows_selected=sum(m['kind']=='table_row' and covered(by_id[i],delivered) for i,m in meta.items()),
        table_rows_available=sum(m['kind']=='table_row' for m in meta.values()),exact_body_duplicate_groups=[a for a in info['aliases'] if a['exact_body_duplicate_count']],
        selection='Bound review passages, complete table dependencies, then source-fair lexical original spans; no generated summaries.')
