"""Opt-in brief interface: model selects IDs; the program binds literal text."""
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path

from ForecastAgent.analysis.pilot import Journal, digest, load, save, WARNING
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.providers.model import ask_model
from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.research_loop import decision_http, forecast_brief as legacy
from ForecastAgent.research_loop.distribution import forecast
from ForecastAgent.research_loop.live_trial import SUPER, forecast_summary

PROTOCOL = 'reference-bound-forecast-brief-v1'
BRIEF_BYTES = legacy.BRIEF_BYTES
OUTPUT_TOKENS = legacy.OUTPUT_TOKENS
REASONING = legacy.REASONING
TEXT = legacy.TEXT
FACT = {'type':'object','additionalProperties':False,'required':
    ['id','role','evidence_ids','interpretation','limitation'], 'properties':{
    'id':TEXT(30),'role':legacy.FACT['properties']['role'],
    'evidence_ids':{'type':'array','minItems':1,'maxItems':3,'items':TEXT(30)},
    'interpretation':TEXT(180),'limitation':TEXT(160)}}
SCHEMA = copy.deepcopy(legacy.SCHEMA)
SCHEMA['properties']['facts']['items'] = FACT
SYSTEM = legacy.SYSTEM.replace(
    'For every fact copy a short contiguous quote from ONE supplied evidence_id. Never stitch\n'
    'quotes or insert an entity. Put your interpretation and limits outside the quotation.\n'
    'Use period/unit only when literally present in that referenced span; otherwise leave empty.',
    'For each fact select 1-3 exact supplied reference IDs in evidence_ids. The program binds\n'
    'the literal original text and coordinates. Do not copy quotations, offsets, dates or units\n'
    'into separate fields. Keep interpretation and limitations separate from original text.\n'
    'These are navigation references, not verified facts. Include a header/date reference when\n'
    'needed to interpret a row. If timing or units are unclear, state that in limitation.')


def blocks(text, limit=1400):
    """Partition only visible text; preserve every character and complete short rows."""
    start = cursor = 0
    for line in text.splitlines(keepends=True):
        end = cursor + len(line)
        special = line.lstrip().startswith(('|','#'))
        if cursor > start and (special or end-start > limit):
            yield start,cursor
            start = cursor
        if len(line) > limit:
            # An unbroken long line stays visibly partial, never a certified row.
            while end-start > limit:
                right = start+limit
                yield start,right
                start = right
        cursor = end
        if special or not line.strip():
            if start < cursor:
                yield start,cursor
            start = cursor
    if start < len(text):
        yield start,len(text)


def catalog(common):
    """Bind IDs to the same visible originals, without reading hidden bodies."""
    sources = {s['source_id']:s for s in common['sources']}
    if len(sources) != len(common['sources']):
        raise ValueError('Duplicate source identity')
    references = []; parents = set()
    for span in common['evidence']:
        if span['evidence_id'] in parents or span['source_id'] not in sources:
            raise ValueError('Ambiguous visible evidence identity')
        parents.add(span['evidence_id'])
        if type(span['start']) is not int or type(span['end']) is not int or span['start']<0 or span['end']-span['start']!=len(span['text']):
            raise ValueError('Visible evidence coordinates mismatch')
        partitions = list(blocks(span['text']))
        if ''.join(span['text'][a:b] for a,b in partitions) != span['text']:
            raise ValueError('Reference partition changed visible original text')
        headers = []
        for a,b in partitions:
            text = span['text'][a:b]
            if not text.strip():
                continue
            rid = f'R{len(references)+1:04}'
            row = text.lstrip().startswith('|') and text.rstrip().endswith('|')
            is_header = row and not re.search(r'\|\s*[-+]?\d+(?:[.,]\d+)?\s*(?:\||$)',text)
            contexts = list(headers) if row and not is_header else []
            if is_header:
                headers.append(rid)
            elif not row:
                headers = []
            source = sources[span['source_id']]
            references.append({'reference_id':rid,'evidence_id':span['evidence_id'],
                'source_id':span['source_id'],'url':source['url'],
                'body_sha256':source['body_sha256'],'start':span['start']+a,'end':span['start']+b,
                'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
                **{k:span[k] for k in ('document_index','coordinate_space') if k in span},
                'context_ids':contexts,'kind':'table_header' if is_header else 'table_row' if row else 'text_block',
                'literal_date_tokens':list(dict.fromkeys(re.findall(
                    r'\b(?:\d{4}-\d{2}-\d{2}|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:,)?\s+\d{4}|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b',text)))[:8],
                'literal_unit_tokens':list(dict.fromkeys(re.findall(
                    r'(?i)\b(?:meters|metres|feet|ft|yards|percent|millimeters/year|countries|MLLW|MHHW)\b|%',text)))[:8],
                'event_period_status':'not_semantically_verified','value_unit_status':'not_semantically_verified',
                'meaning_verified':False})
    return {'schema':PROTOCOL,'original_state_sha256':digest(common),'references':references,
            'new_source_text':False,'source_text_selection_changed':False}


def model_view(common, index):
    view = copy.deepcopy(common)
    view['evidence'] = [{'evidence_id':r['reference_id'],'source_id':r['source_id'],
                        'text':r['text'],**({'context_ids':r['context_ids']} if r['context_ids'] else {})}
                       for r in index['references']]
    # The packet is a display partition of the same text, not extra evidence.
    return view


def tool_for(index):
    schema = copy.deepcopy(SCHEMA)
    schema['properties']['facts']['items']['properties']['evidence_ids']['items']['enum'] = [r['reference_id'] for r in index['references']]
    return {'type':'function','function':{'name':'record_forecast_brief',
        'description':'Select original reference IDs and record a short fallible forecast brief. Do not copy quotes or invent dates/units.',
        'parameters':schema}}


def validate_brief(proposal, common):
    if not isinstance(proposal,dict) or set(proposal)!=set(SCHEMA['properties']):
        raise ValueError('Forecast brief top-level fields mismatch')
    if not isinstance(proposal['facts'],list) or len(proposal['facts'])>6:
        raise ValueError('Facts must be a bounded array; no string-to-fact repair')
    index = catalog(common)
    refs = {r['reference_id']:r for r in index['references']}
    facts, rejected, used = [], [], set()
    for position,entry in enumerate(proposal['facts']):
        try:
            check_schema(entry,FACT)
            if not entry['id'].strip() or entry['id'] in used:
                raise ValueError('Empty or duplicate fact ID')
            ids=entry['evidence_ids']
            if len(set(ids))!=len(ids) or any(r not in refs for r in ids):
                raise ValueError('Use unique visible reference IDs; hidden or guessed IDs are invalid')
            facts.append(copy.deepcopy(entry));used.add(entry['id'])
        except (ValueError,KeyError,TypeError) as exc:
            rejected.append({'section':'facts','index':position,'error':str(exc)})
    factors=[]
    for position,entry in enumerate(proposal['change_factors'] if isinstance(proposal['change_factors'],list) else []):
        try:
            check_schema(entry,legacy.FACTOR)
            if not entry['fact_ids'] or len(set(entry['fact_ids']))!=len(entry['fact_ids']) or not set(entry['fact_ids'])<=used:
                raise ValueError('Change factor needs unique retained fact IDs')
            if len(factors)>=3:raise ValueError('Change factor cap')
            factors.append(copy.deepcopy(entry))
        except (ValueError,KeyError,TypeError) as exc:
            rejected.append({'section':'change_factors','index':position,'error':str(exc)})
    frame=proposal['forecast_frame']
    try:check_schema(frame,SCHEMA['properties']['forecast_frame'])
    except (ValueError,TypeError):
        rejected.append({'section':'forecast_frame','error':'Invalid optional forecasting frame'})
        frame='Recheck the retained original evidence; no synthesized frame is delivered.'
    if any(r['section']=='facts' for r in rejected):
        frame='Recheck the retained evidence. Some proposed facts were rejected; no synthesized forecasting frame is delivered.'
    uncertainties=[]
    if not isinstance(proposal['uncertainties'],list):
        rejected.append({'section':'uncertainties','error':'Optional uncertainty list is malformed; original text and fact limits remain available.'})
    else:
        for position,value in enumerate(proposal['uncertainties']):
            try:
                check_schema(value,TEXT(180))
                if len(uncertainties)>=5:raise ValueError('Uncertainty item cap')
                uncertainties.append(value)
            except (ValueError,TypeError) as exc:
                rejected.append({'section':'uncertainties','index':position,'error':str(exc)})
    result={'facts':facts,'change_factors':factors,'forecast_frame':frame,'uncertainties':uncertainties}
    check_schema(result,SCHEMA)
    if not facts:raise ValueError('No valid references remain; preserve direct-evidence scoring')
    selected=[]
    for fact in facts:
        for rid in fact['evidence_ids']:
            selected.extend([rid,*refs[rid]['context_ids']])
    bound=[refs[rid] for rid in dict.fromkeys(selected)]
    bindings={r['reference_id']:{k:r[k] for k in ('evidence_id','start','end','document_index','coordinate_space') if k in r} for r in bound}
    delivered={'schema':PROTOCOL,'meaning_verified':False,**result,'reference_bindings':bindings}
    if len(json.dumps(delivered,ensure_ascii=False).encode())>BRIEF_BYTES:
        raise ValueError('Reference-bound brief exceeds its declared byte cap')
    return result,{'schema':PROTOCOL,'retained_fact_ids':[f['id'] for f in facts],
        'rejected':rejected,'bound_originals':bound,'scoring_reference_bindings':bindings,
        'exact_original_state_sha256':digest(common),'meaning_verified':False,
        'model_generated_dates_or_units':False,'format_correction_calls':0}


def brief_stage(common,folder):
    folder=Path(folder);index=catalog(common)
    save(folder/'reference-catalog.json',index)
    messages=[{'role':'system','content':SYSTEM},
              {'role':'user','content':json.dumps(model_view(common,index),ensure_ascii=False)}]
    tool=tool_for(index)
    request={'model':SUPER,'messages':messages,'tools':[tool],
        'forced_tool':'record_forecast_brief','max_output_tokens':OUTPUT_TOKENS,
        'reasoning':REASONING,'http_cap':1,'original_state_sha256':digest(common),
        'brief_protocol':PROTOCOL}
    if (folder/'request.json').exists() and load(folder/'request.json')!=request:
        raise ValueError('Frozen reference brief request changed')
    save(folder/'request.json',request)
    if not (folder/'message.json').exists():
        previous={k:os.environ.get(k) for k in ('FORECAST_MODEL','FORECAST_MODEL_FALLBACK_SUPER')}
        os.environ.update(FORECAST_MODEL=SUPER,FORECAST_MODEL_FALLBACK_SUPER='0')
        try:
            message=ask_model(messages,os.environ['OPENROUTER_API_KEY'],tools=[tool],
                forced_tool='record_forecast_brief',observer=Journal(folder/'http',1),
                deadline=time.monotonic()+240,max_output_tokens=OUTPUT_TOKENS,reasoning=REASONING)
            save(folder/'message.json',message)
        finally:
            for key,value in previous.items():
                if value is None:os.environ.pop(key,None)
                else:os.environ[key]=value
    calls=load(folder/'message.json').get('tool_calls',[])
    if len(calls)!=1 or calls[0].get('function',{}).get('name')!='record_forecast_brief':
        raise ValueError('Exactly one reference brief tool response required')
    proposal=json.loads(calls[0]['function']['arguments']);save(folder/'proposal.json',proposal)
    accepted,audit=validate_brief(proposal,common)
    save(folder/'accepted.json',accepted);save(folder/'audit.json',audit)
    return accepted


def score(question,common,heads,spec,folder,brief=None):
    if brief is None:
        return legacy.score(question,common,heads,spec,folder)
    brief,audit=validate_brief(brief,common)
    state=copy.deepcopy(common)
    state['forecast_brief']={'schema':PROTOCOL,'meaning_verified':False,**brief,
                            'reference_bindings':audit['scoring_reference_bindings']}
    if any(state[k]!=v for k,v in common.items()):raise ValueError('Brief changed common original evidence')
    if chain.request_bytes(state,heads)>28000:raise ValueError('Scoring request exceeds common cap')
    folder=Path(folder);save(folder/'state.json',state);save(folder/'binding-audit.json',audit)
    response=decision_http.call(state,folder/'decision',heads)
    if spec is None:raw={'probability_yes':response['answers']['event_yes']['noul']}
    else:
        dist=forecast(response,spec)
        raw=({'probability_yes_per_category':dist['probabilities']} if spec['kind']=='multiple_choice'
             else {'continuous_cdf':dist['raw_cdf']})
    result={'schema':PROTOCOL,'status':'completed','raw_forecast':raw,'payload':payload(question,raw),
        'original_state_sha256':digest(common),'forecast_brief_delivered':True,
        'submitted':False,'evaluation_warning':WARNING,'conditional_reread':False}
    result['summary']=forecast_summary(result,question);save(folder/'result.json',result)
    return result
