"""Conservative quote alignment with original text coordinates; no paraphrase repair."""
import re


def _unescape(text):
    return re.sub(r'\\(?:u[0-9a-fA-F]{4}|n|r|t)',
        lambda m: chr(int(m.group()[2:],16)) if m.group()[1]=='u' else {'n':'\n','r':'\r','t':'\t'}[m.group()[1]],text)


def _normalized(text):
    chars=[];starts=[];ends=[]
    for i,c in enumerate(text):
        if c.isspace():
            if chars and chars[-1]==' ':ends[-1]=i+1;continue
            chars.append(' ')
        else:chars.append(c)
        starts.append(i);ends.append(i+1)
    return ''.join(chars),starts,ends


def bind(source,quote):
    text=source.get('text','')
    if not isinstance(quote,str) or len(quote.strip())<12:return {'bound':False,'spans':[],'issue':'short_or_missing_quote'}
    normalized,starts,ends=_normalized(text)
    def locate(value):
        needle=_normalized(value.strip())[0];positions=[];at=normalized.find(needle)
        while at>=0:
            positions.append(at);at=normalized.find(needle,at+1)
        if len(positions)!=1:return None
        a=starts[positions[0]];b=ends[positions[0]+len(needle)-1]
        return {'start':a,'end':b,'text':text[a:b],
                'capture_start':source.get('start',0)+a,'capture_end':source.get('start',0)+b}
    for candidate in (quote,_unescape(quote)):
        exact=locate(candidate)
        if exact:return {'bound':True,'spans':[exact],'representation_repair':candidate!=quote or exact['text']!=quote,'discontinuous':False}
    # Preserve explicit paragraph separation. Never concatenate discontinuous
    # fragments into a quotation that did not occur in the original document.
    parts=[p.strip() for p in re.split(r'\n\s*\n',_unescape(quote)) if p.strip()]
    if 1<len(parts)<=3 and all(len(p)>=12 for p in parts):
        found=[locate(p) for p in parts]
        if all(found) and all(found[i]['end']<=found[i+1]['start'] for i in range(len(found)-1)):
            return {'bound':True,'spans':found,'representation_repair':True,'discontinuous':True}
    return {'bound':False,'spans':[],'issue':'quote_not_uniquely_aligned'}
