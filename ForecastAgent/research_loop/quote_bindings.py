"""Reversible display-format matching against inspected saved spans only."""
import copy
import re

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop.schema import NODE

POLICY = 'inspected_quote_format_v1'
LINK = re.compile(r'(?<!!)\[([^\[\]\n]+)\]\((https?://[^\s()]+)\)')


def display(text, strip_links=True):
    """Fold whitespace and HTTP Markdown links, retaining original coordinates."""
    chars, positions = [], []
    cursor = 0
    for match in LINK.finditer(text) if strip_links else []:
        chars.extend(text[cursor:match.start()])
        positions.extend((i,i+1) for i in range(cursor,match.start()))
        label = match.group(1)
        for index,char in enumerate(label):
            chars.append(char)
            positions.append((match.start() if index==0 else match.start(1)+index,
                match.end() if index==len(label)-1 else match.start(1)+index+1))
        cursor=match.end()
    chars.extend(text[cursor:])
    positions.extend((i,i+1) for i in range(cursor,len(text)))
    rendered, offsets = [], []
    for char, bounds in zip(chars,positions):
        if char.isspace():
            if rendered and rendered[-1]==' ':
                offsets[-1]=(offsets[-1][0],bounds[1])
                continue
            char=' '
        rendered.append(char);offsets.append(bounds)
    return ''.join(rendered),offsets


def locate(quote, spans):
    """Require one reversible contiguous match; never repair changed facts."""
    # Never erase a URL expressly included in the submitted quotation.
    strip_links = not bool(LINK.search(quote))
    needle=display(quote,strip_links)[0]
    if not needle.strip():
        return None
    matches=[]
    for span in spans:
        rendered, offsets=display(span['text'],strip_links)
        start=rendered.find(needle)
        while start>=0:
            a,b=offsets[start][0],offsets[start+len(needle)-1][1]
            literal=span['text'][a:b]
            if display(literal,strip_links)[0]==needle:
                matches.append((span,a,b,literal))
            if len(matches)>1:
                return None  # An ambiguous location requires a narrower reading.
            start=rendered.find(needle,start+1)
    return matches[0] if len(matches)==1 else None


def prepare(task, proposal):
    """Restore exact source formatting in a copy; preserve the submitted reply."""
    from ForecastAgent.research_loop import state, fusion
    material=state.catalog(task.bundle,task.cutoff)
    delivered={r['evidence_id'] for r in fusion.inspected_references(task,material)}
    delivered.update(r['evidence_id'] for r in
        task.bundle['research_acquisition'].get('inspected_context_references',[])
        if r['evidence_id'] in material['spans'])
    delivered.update(r['evidence_id'] for n in
        (task.bundle['research_loop'].get('current') or {}).get('nodes',[]) for r in n['bindings']
        if r['evidence_id'] in material['spans'])
    result=copy.deepcopy(proposal); records=[]
    for node in result['nodes']:
        if not isinstance(node,dict) or node.get('kind')!='observation':
            continue
        refs=node.get('evidence_ids',[])
        if not isinstance(refs,list) or any(not isinstance(r,str) for r in refs):
            continue  # Let the native node isolator quarantine this sibling.
        spans=[material['spans'][r] for r in refs
               if r in delivered and r in material['spans']]
        for field in ('claim','event_time','stage_basis'):
            quote=node.get(field)
            if not isinstance(quote,str) or not quote.strip() or any(quote in s['text'] for s in spans):
                continue
            found=locate(quote,spans)
            if not found:
                continue
            span,a,b,literal=found
            if len(literal)>NODE['properties'][field]['maxLength']:
                continue  # A restored quote must still satisfy the native cap.
            node[field]=literal
            records.append({'policy':POLICY,'node_id':node.get('id'),'field':field,
                'submitted_value':quote,'literal_value':literal,'evidence_id':span['evidence_id'],
                'url':span['url'],'body_sha256':span['body_sha256'],
                'start':span['start']+a,'end':span['start']+b,
                'coordinate_space':span.get('coordinate_space','saved_body_characters'),
                'view_sha256':span.get('view_sha256'),
                'parser':span.get('parser'),
                'json_provenance':copy.deepcopy(span.get('json_provenance',[])),
                'literal_sha256':digest(literal),'meaning_verified':False})
    return result,records
