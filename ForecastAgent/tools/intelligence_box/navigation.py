"""Offline navigation of hash-verified originals; no fetching or fact inference."""
from io import BytesIO
import hashlib
import re
from contextlib import contextmanager
from ForecastAgent.readers.encoding import decode_response

VERSION = "saved_navigation_v1"


def bounded(value, lower, upper, label):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{label} must be in {lower}..{upper}")
    return value


@contextmanager
def units(capture, raw, max_pages):
    """Yield structural units and parser coverage, without mutating originals."""
    raw, encoding = decode_response(raw, capture.get("http", {}).get("response_headers"), maximum=32_000_000)
    if capture.get('http',{}).get('status',200)!=200:
        raise ValueError('Cannot treat an HTTP error body as a source document')
    if capture.get("http", {}).get("truncated"):
        raise ValueError("Cannot navigate an incomplete raw response")
    info = {"decoded_sha256": hashlib.sha256(raw).hexdigest(), "content_encoding": encoding,
            "coordinates": "normalized unit text offsets, not raw byte offsets", "ocr_performed": False}
    if raw.startswith(b"%PDF-"):
        import pdfplumber
        with pdfplumber.open(BytesIO(raw)) as pdf:
            count = len(pdf.pages)
            info.update(format="pdf", total_pages=count, pages_scanned=min(count,max_pages), scan_truncated=count>max_pages)
            yield [{"unit_id":f"page:{i+1}", "kind":"page", "title":f"Page {i+1}",
                    "page":i+1, "text":None, "pdf_page":page} for i,page in enumerate(pdf.pages[:max_pages])], info
        return
    from lxml import etree, html
    content_type = capture.get("http", {}).get("content_type", "").lower()
    is_xml = "xml" in content_type and "html" not in content_type
    if is_xml:
        if re.search(br"<!\s*(DOCTYPE|ENTITY)\b",raw,re.I):
            raise ValueError("DTD and entity declarations forbidden")
        root=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,no_network=True))
        nodes=root.xpath("//*[@id]")
        result=[{"unit_id":f"xml:{i+1}", "kind":"provision", "title":node.get("id"),
                 "element_id":node.get("id"), "xpath":root.getroottree().getpath(node),
                 "text":" ".join(" ".join(node.itertext()).split())} for i,node in enumerate(nodes)]
        if not result:
            result=[{"unit_id":"document:1","kind":"document","title":"XML document","text":" ".join(" ".join(root.itertext()).split())}]
    elif "html" in content_type or re.match(br"\s*<(?:!doctype\s+html|html)\b", raw, re.I):
        root=html.fromstring(raw)
        for node in root.xpath("//script|//style|//noscript|//svg"):
            node.drop_tree()
        result=[{"unit_id":"section:1","kind":"section","title":"Preamble","text":""}]
        current_section=result[0]
        tree=root.getroottree()
        # Traverse leaf blocks to avoid duplicating ancestor text. Tables are
        # separate units; nested table cells are not silently flattened.
        section_count=1
        table_count=0
        for node in root.iter():
            tag=node.tag.lower() if isinstance(node.tag,str) else ""
            if not tag: continue
            if any(a.tag=="table" for a in node.iterancestors()): continue
            if tag in {"h1","h2","h3","h4","h5","h6"}:
                text=" ".join(node.text_content().split())
                section_count+=1
                result.append({"unit_id":f"section:{section_count}","kind":"section","title":text,"xpath":tree.getpath(node),"text":text})
                current_section=result[-1]
            elif tag=="table":
                table_count+=1
                rows=[]
                for row in node.xpath('.//tr'):
                    if next((a for a in row.iterancestors() if a.tag=='table'),None) is not node: continue
                    rows.append([{"text":" ".join(c.text_content().split()),"tag":c.tag,
                                  "rowspan":c.get('rowspan','1'),"colspan":c.get('colspan','1')} for c in row.xpath('./th|./td')])
                result.append({"unit_id":f"table:{table_count}","kind":"table","title":current_section['title'],"xpath":tree.getpath(node),"rows":rows,"text":"\n".join(' | '.join(c['text'] for c in row) for row in rows)})
            elif tag=='pre':
                # Generic legal/document heading labels, not question-specific
                # facts. Case-sensitive markers avoid mixed-case TOC entries.
                for line_number,line in enumerate(node.text_content().splitlines(),1):
                    stripped=line.strip()
                    if re.match(r'^(?:SECTION\s+\d|SEC\.\s+\d|TITLE\s+[IVX\d]+\b|CHAPTER\s+\w|PART\s+\w|ARTICLE\s+\w|Subtitle\s+\w)',stripped):
                        section_count+=1
                        current_section={'unit_id':f'section:{section_count}','kind':'section','title':stripped,
                                         'xpath':tree.getpath(node),'line_start':line_number,'line_end':line_number,
                                         'heading_detection':'preformatted_label_heuristic','text':line}
                        result.append(current_section)
                    else:
                        current_section['text']+='\n'+line
                    if current_section.get('xpath')==tree.getpath(node): current_section['line_end']=line_number
            elif tag in {"p","li","pre","div","span","body"} and not node.xpath('.//p|.//li|.//pre|.//div|.//table|.//h1|.//h2|.//h3|.//h4|.//h5|.//h6'):
                if tag=='span' and any(a.tag in {'p','li','pre','div','span'} for a in node.iterancestors()): continue
                text=" ".join(node.text_content().split())
                if text: current_section['text']+='\n'+text
        if not any(x['text'].strip() for x in result):
            result[0]['text']=' '.join(root.text_content().split())
        result.append({'unit_id':'document:1','kind':'document','title':'Complete visible HTML text',
                       'text':root.text_content(),'extraction_role':'full_text_fallback'})
    elif content_type.startswith("text/plain"):
        result=[{"unit_id":"document:1","kind":"document","title":"Plain text","text":raw.decode('utf-8',errors='replace')}]
    else:
        raise ValueError("Supported originals: HTML, XML, PDF and plain text")
    info.update(format="xml" if is_xml else "html" if 'html' in content_type else "text",scan_truncated=False,total_units=len(result))
    yield result,info


def text_of(unit):
    if unit['text'] is None:
        unit['text']=unit['pdf_page'].extract_text() or ''
    return unit['text']


def navigate(capture,raw,action,*,offset=0,limit=40,query=None,unit_id=None,start=0,max_chars=12000,row_start=0,max_rows=40,max_pages=100):
    bounded(max_pages,1,500,"max_pages")
    bounded(offset,0,100000,"offset"); bounded(limit,1,100,"limit")
    bounded(start,0,32000000,"start"); bounded(max_chars,100,50000,"max_chars")
    bounded(row_start,0,100000,"row_start"); bounded(max_rows,1,200,"max_rows")
    base={"parser_version":VERSION,"capture_id":capture['id'],"raw_sha256":capture['raw_sha256'],
          "source_url":capture.get('request_url'),"captured_at_utc":capture.get('captured_at_utc'),
          "source_capture_status":capture.get('status'),"network_requests":0,
          "truth_verified":False,"target_relevance_verified":False}
    with units(capture,raw,max_pages) as (items,coverage):
        base['coverage']=coverage
        if action=='outline':
            selected=items[offset:offset+limit]
            base.update(status="indexed",total_units=len(items),next_offset=offset+limit if offset+limit<len(items) else None,
                        units=[{k:v for k,v in x.items() if k not in {'text','rows','pdf_page'}} for x in selected])
        elif action=='search':
            if not isinstance(query,str) or not query.strip() or len(query)>200: raise ValueError("query must be a nonempty literal of at most 200 characters")
            hits=[]; more=False
            for item in items:
                text=text_of(item)
                for match in re.finditer(re.escape(query),text,re.I):
                    if len(hits)>=limit: more=True; break
                    a,b=match.span()
                    hits.append({"unit_id":item['unit_id'],"title":item['title'],"start":a,"end":b,
                                 "excerpt":text[max(0,a-160):b+240],"page":item.get('page'),"xpath":item.get('xpath')})
                if more: break
            base.update(status="found" if hits else "not_found",hits=hits,hits_truncated=more)
        elif action=='part':
            selected=next((x for x in items if x['unit_id']==unit_id),None)
            if selected is None:
                base.update(status="not_found",unit_id=unit_id); return base
            text=text_of(selected)
            if start>len(text): raise ValueError("start exceeds unit text length")
            base.update(status="readable" if text.strip() else "unreadable",unit_id=unit_id,
                        title=selected['title'],page=selected.get('page'),xpath=selected.get('xpath'),
                        line_start=selected.get('line_start'),line_end=selected.get('line_end'),
                        start=start,end=min(start+max_chars,len(text)),total_chars=len(text),text=text[start:start+max_chars],
                        next_start=start+max_chars if start+max_chars<len(text) else None)
            rows=selected.get('rows')
            if selected['kind']=='page':
                tables=selected['pdf_page'].extract_tables()
                base['page_tables']=[{"table_id":f"{unit_id}:table:{i+1}","total_rows":len(t),"rows":t[row_start:row_start+max_rows],"rows_truncated":row_start+max_rows<len(t)} for i,t in enumerate(tables)]
                base['table_warning']='PDF grid extraction is heuristic; no OCR or merged-cell inference.'
            if rows is not None:
                if row_start>len(rows): raise ValueError("row_start exceeds table row count")
                base.update(total_rows=len(rows),row_start=row_start,rows=rows[row_start:row_start+max_rows],
                            next_row=row_start+max_rows if row_start+max_rows<len(rows) else None,
                            header_rows=[r for r in rows[:10] if any(c['tag']=='th' for c in r)],merged_cells_normalized=False)
        else: raise ValueError("Unknown navigation action")
    return base


def definitions():
    common={"capture_id":{"type":"string"},"max_pages":{"type":"integer","minimum":1,"maximum":500}}
    specs=[('outline','List saved original sections, pages and tables. Paginate using next_offset; PDF page scan bounds are explicit.',{'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100}},['capture_id']),
           ('search','Literal local search of saved originals. Returns unit IDs and normalized text offsets; not semantic verification.',{'query':{'type':'string','maxLength':200},'limit':{'type':'integer','minimum':1,'maximum':100}},['capture_id','query']),
           ('part','Read an exact saved unit and row window. Follow next_start/next_row for complete coverage; PDF page tables are heuristic.',{'unit_id':{'type':'string'},'start':{'type':'integer','minimum':0},'max_chars':{'type':'integer','minimum':100,'maximum':50000},'row_start':{'type':'integer','minimum':0},'max_rows':{'type':'integer','minimum':1,'maximum':200}},['capture_id','unit_id'])]
    return [{'type':'function','function':{'name':'intelligence_'+name,'description':description+' Zero network calls. Requires intact saved bytes.',
            'parameters':{'type':'object','properties':dict(common,**props),'required':required,'additionalProperties':False}}} for name,description,props,required in specs]
