"""Local financial row discovery and immutable numeric/reference binding.

Candidates are exact saved text, not verified issuer/period interpretations.
No network or model calls occur here. Model-selected semantics remain labeled.
"""
import hashlib
import re
from urllib.parse import urlsplit

from ForecastAgent.market_pulse.analysis import digest, analysis_view

NUMBER = re.compile(r'(?<![\w])(?:\(\s*)?-?\d[\d,]*(?:\.\d+)?(?:\s*\))?(?![\w])')
METRIC = re.compile(r'\b(?:total net sales|net sales|revenue|net income|operating income|'
                    r'diluted|income taxes|tax rate|gross margin|earnings per share|estimated EPS|actual EPS)\b', re.I)
UNITS = re.compile(r'\b(?:millions?|billions?|thousands?|per.share|EPS|shares|percent)\b|\$', re.I)
PERIOD = re.compile(r'\b(?:20\d{2}|(?:three|six|nine|twelve) months|quarter|FY\s*20\d{2})\b', re.I)
SCALES = {'USD': 1, 'USD_millions': 1e6, 'USD_billions': 1e9,
          'USD_per_share': 1, 'shares': 1, 'shares_thousands': 1e3,
          'shares_millions': 1e6, 'percent': 1}
METRICS = {'revenue', 'diluted_eps', 'net_income', 'diluted_shares', 'operating_income',
           'expense', 'tax_rate', 'margin', 'growth_rate', 'one_off', 'estimate'}


def original_ref(url, index, text, start, end):
    return {'url': url, 'document_index': index, 'start': start, 'end': end,
        'quote': text[start:end], 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}


def numeric_tokens(text):
    result = []
    links=[(m.start(),m.end()) for m in re.finditer(r'https?://[^\s)]+',text)]
    for match in NUMBER.finditer(text):
        if any(a<=match.start()<b for a,b in links): continue
        if '|' in text:
            left=text.rfind('|',0,match.start())+1
            right=text.find('|',match.end());right=len(text) if right<0 else right
            cell=text[left:right]
            # Digits in product/metric labels are not quantitative data cells.
            if re.search(r'[A-Za-z]',cell) and not re.fullmatch(r'\s*(?:USD\s*)?\$?\s*[-()\d.,% ]+\s*',cell): continue
        raw = match.group().strip(); negative = raw.startswith('(')
        value = float(raw.replace(',', '').replace('(', '').replace(')', '').strip())
        result.append({'raw': raw, 'value': -value if negative else value,
                       'start': match.start(), 'end': match.end()})
    return result


def discover(bundle):
    view, exclusions = analysis_view(bundle); candidates = []; seen = set()
    for url, page in view['pages'].items():
        body = page['content']; docs = [(None, body)]
        # Prefer the compact first parsed cell view to repeated expanded grids.
        tables = set()
        for i, doc in enumerate(page.get('documents', []), 1):
            meta = doc.get('metadata', {})
            if 'table' not in meta.get('format', ''): continue
            table = meta.get('table', i)
            if table in tables: continue
            tables.add(table); docs.append((i, doc['page_content']))
        for index, text in docs:
            offset = 0
            for line in text.splitlines(keepends=True):
                start, end = offset, offset + len(line); offset = end
                if not METRIC.search(line) or not numeric_tokens(line) or len(line) > 1800: continue
                # EPS and quarter-end rows in dated earnings-history tables.
                signature = (url, line.strip())
                if signature in seen: continue
                seen.add(signature)
                context_start = max(0, text.rfind('\n\n', max(0, start-1700), start))
                context_start = max(context_start, start-1200)
                context_end = min(len(text), end+240)
                # Table orientation belongs to the selected coordinate space.
                refs = [original_ref(url, index, text, start, end),
                        original_ref(url, index, text, context_start, context_end)]
                if index is not None: refs.append(original_ref(url, index, text, 0, min(700, len(text))))
                # Caption/units can reside outside the parsed table document.
                if index is not None:
                    caption = []
                    for match in UNITS.finditer(body):
                        a = body.rfind('\n', 0, match.start())+1
                        b = body.find('\n', match.end()); b = len(body) if b<0 else b
                        snippet = body[a:b]
                        if len(snippet)<450 and re.search(r'in (?:millions|billions|thousands)|except.*per.share', snippet, re.I):
                            caption.append((a,b))
                    for a,b in list(dict.fromkeys(caption))[:2]: refs.append(original_ref(url,None,body,a,b))
                # Original document intro helps identify fiscal periods/publication.
                refs.append(original_ref(url,None,body,0,min(950,len(body))))
                refs = list({(r['document_index'],r['start'],r['end']):r for r in refs}.values())
                current_year = max((int(y) for y in re.findall(r'\b20\d{2}\b', body[:3500])), default=0)
                primary = urlsplit(url).hostname in {'www.sec.gov','sec.gov'} or 'microsoft.com' in (urlsplit(url).hostname or '')
                guidance = bool(re.search(r'expected to be between|expect.*(?:quarter|range)', line, re.I))
                eps = 'earnings per share' in bundle['request']['question'].lower()
                exact = bool(re.search(r'diluted|net income|tax rate|gross margin' if eps else
                                       r'total net sales|(?:^|[-\u2022]\s*)Revenue was|\| Revenue \|', line, re.I))
                # A parenthesized footnote marker without a data cell is not a financial value.
                if len(numeric_tokens(line))==1 and re.search(r'\(\d\)\s*$',line.strip()): continue
                candidates.append({'url':url,'document_index':index,'row':line.rstrip('\r\n'),
                    'numbers':numeric_tokens(line), 'refs':refs,
                    'source_role_hint':'issuer_filing_or_release' if primary else 'secondary_unverified_basis',
                    'source_role_verified':False, 'publication_time_verified':None,
                    'captured_at_utc':page.get('retrieved_at_utc'),
                    'priority':(int(guidance),int(exact),int(primary),current_year),
                    'interpretation':'Row and nearby headers are original text; metric, period, basis and column applicability still require interpretation.'})
    candidates.sort(key=lambda r: tuple(-v for v in r['priority']))
    for i,row in enumerate(candidates,1):
        row['record_id']=f'F{i:03}'
        for j,n in enumerate(row['numbers']): n['token_id']=row['record_id']+f'.N{j}'
        for j,r in enumerate(row['refs']): r['ref_id']=row['record_id']+f'.R{j}'
    return {'schema':'financial-original-row-candidates-v1','candidates':candidates,
        'excluded_sources':exclusions,'candidate_count':len(candidates), 'facts_verified':False,
        'search_calls':0,'model_calls':0,'original_request_sha256':digest(bundle['request'])}


def validate_references(bundle, table):
    for row in table['candidates']:
        for ref in row['refs']:
            page=bundle['pages'][ref['url']]
            text=page['content'] if ref['document_index'] is None else page['documents'][ref['document_index']-1]['page_content']
            if ref['quote'] != text[ref['start']:ref['end']] or ref['text_sha256'] != hashlib.sha256(text.encode()).hexdigest():
                raise ValueError('Original financial reference changed')
    return True


def selected_facts(table, rows):
    """Bind model interpretations to existing tokens and explicit unit evidence."""
    tokens={n['token_id']:(row,n) for row in table['candidates'] for n in row['numbers']}
    refs={r['ref_id']:r for row in table['candidates'] for r in row['refs']}
    facts=[]
    for i,item in enumerate(rows,1):
        row,n=tokens.get(item['token_id'],(None,None))
        if row is None or item['metric'] not in METRICS or item['source_unit'] not in SCALES:
            raise ValueError('Unknown financial token, metric or unit')
        unit=refs.get(item['unit_ref']); period=refs.get(item['period_ref'])
        if not unit or not period or unit['url']!=row['url'] or period['url']!=row['url']:
            raise ValueError('Unit/period reference belongs to another source')
        unit_text=unit['quote']; source_unit=item['source_unit']
        required={'USD_millions':r'\bmillions?\b','USD_billions':r'\bbillions?\b',
            'shares_thousands':r'\bthousands?\b','shares_millions':r'\bmillions?\b',
            'USD':r'\$|USD|dollars','USD_per_share':r'per.share|EPS|\$','shares':r'\bshares\b','percent':r'%|percent'}[source_unit]
        if not re.search(required,unit_text,re.I): raise ValueError('Unit has no literal original support')
        if not item['period_text'] or item['period_text'] not in period['quote']:
            raise ValueError('Fiscal period text is not in the referenced original context')
        if item['metric']=='diluted_eps' and source_unit!='USD_per_share':
            raise ValueError('EPS must use per-share dollar units')
        if item['metric'] in {'revenue','net_income','operating_income','expense','one_off','estimate'} and source_unit.startswith('shares'):
            raise ValueError('Currency metric cannot use share count units')
        if item['basis'] not in {'GAAP','adjusted_or_nonGAAP','unknown','not_applicable'} or item['role'] not in {'actual','management_guidance','estimate','other_context'}:
            raise ValueError('Unknown accounting basis or evidence role')
        facts.append({'fact_id':f'V{i}',**item,'raw_value':n['value'],
            'normalized_value':n['value']*SCALES[source_unit],
            'normalized_unit':'USD_per_share' if source_unit=='USD_per_share' else 'shares' if source_unit.startswith('shares') else 'percent' if source_unit=='percent' else 'USD',
            'conversion_factor':SCALES[source_unit], 'source_row_id':row['record_id'],
            'source_url':row['url'],'numeric_token':n,'original_row_ref':row['refs'][0],
            'unit_original_ref':unit,'period_original_ref':period,
            'numeric_binding_valid':True,'semantic_interpretation_verified':False})
    return facts
