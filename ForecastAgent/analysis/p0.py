"""Experimental rule contracts, directed saved-text reading and coverage routing."""
import copy
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.pilot import digest, load, save

PROTOCOL='mercury-analysis-p0-v1'
DATE=re.compile(r'20\d\d(?:-\d\d(?:-\d\d)?)?|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+(?:\d{1,2},?\s+)?20\d\d|20\d\d\s*年\s*\d+\s*月',re.I)
MONTH=re.compile(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b',re.I)
NUMBER=re.compile(r'(?<!\w)[+-]?\d+(?:[,.]\d+)*(?:\s*(?:%|million|billion|USD|dollars))?',re.I)
UNIT=re.compile(r'%|\$|\b(?:percent(?:age)?|million|billion|dollars?|USD|gallons?|index|cases|points?|counts?)\b',re.I)
ACTION=re.compile(r'\b(?:filed|submitted|signed|entered|effective|appointed|announced|struck|strike|attack|released|published|reported|achieved)\b',re.I)
RELEASE=re.compile(r'\b(?:release|released|publication|published|reporting date|vintage)\b',re.I)


def balanced_packet(bundle):
    """Bound serialized span size across languages while retaining every original character."""
    packet=chain.full_packet(bundle);library=[]
    for span in packet['evidence']:
        position=0;text=span['text']
        while position<len(text):
            low=1;high=len(text)-position
            while low<high:
                mid=(low+high+1)//2
                if len(json.dumps(text[position:position+mid]).encode())<=1800:low=mid
                else:high=mid-1
            end=position+low
            boundary=text.rfind('\n',position+min(120,low//2),end)
            if boundary>=0 and end<len(text):end=boundary+1
            row={**span,'evidence_id':f'E{len(library)+1:04}',
                 'start':span['start']+position,'end':span['start']+end,'text':text[position:end]}
            library.append(row);position=end
    packet['evidence']=library
    return packet


def contract(question):
    """Quote rules rather than infer missing dates, actors or logical conditions."""
    fields={k:question.get(k,'') for k in ('question','resolution_criteria','fine_print')}
    text=' '.join(str(v) for v in fields.values())
    def snippets(pattern):
        rows=[]
        for field,value in fields.items():
            if not isinstance(value,str):continue
            for match in re.finditer(r'[^\n.!?]+(?:[.!?]|$)',value):
                if re.search(pattern,match[0],re.I):
                    rows.append({'field':field,'start':match.start(),'end':match.start()+len(match[0][:260]),
                                 'quote':match[0][:260],'quote_truncated':len(match[0])>260})
                if len(rows)>=3:return rows
        return rows
    release_basis=bool(re.search(r'release date|first release|in (?:the )?\w+ release|release.*(?:occur|in |during)|published (?:in|during)',text,re.I))
    urls=re.findall(r'https?://[^\s<>\])]+',str(question.get('resolution_criteria','')))
    primary_urls=list(dict.fromkeys(question.get('source_links',[])))[:8]
    tokens=[t.lower() for t in re.findall(r'\b[a-zA-Z][a-zA-Z-]{3,}\b',fields['question'])]
    stop=set('will what when where which with from before after according between during under greater less than this that question report company change period ending release percentage'.split())
    return {'schema':'quoted-rule-contract-v1','interpretation_status':'Lexical cues only; exact question rules remain authoritative.',
            'time_basis':'release_window' if release_basis else 'event_or_observation_window',
            'date_mentions':list(dict.fromkeys(m[0] for m in DATE.finditer(text)))[:10],
            'target_months':list(dict.fromkeys(m[0].lower() for m in MONTH.finditer(text)))[:5],
            'metric_terms':list(dict.fromkeys(t for t in tokens if t not in stop))[:16],
            'unit_mentions':list(dict.fromkeys(m[0] for m in UNIT.finditer(text)))[:8],
            'time_constraints':snippets(r'\b(?:before|after|by|on|release|date|period|month|UTC)\b'),
            'actor_action_constraints':snippets(r'\b(?:will|conduct|file|submit|appoint|strike|attack|achieve|reach|record|report)\b'),
            'observation_constraints':snippets(r'\b(?:observation|period|for the|as of|ending|quarter|month)\b'),
            'stage_and_exception_constraints':snippets(r'\b(?:unless|except|first|effective|file|signed|either|or|not)\b'),
            'rule_source_urls':urls[:5],'declared_primary_source_leads':primary_urls,
            'unknown_fields':['Unquoted actor, action, logical branches and time semantics require model interpretation.'],
            'policy':'Publication date, observation period, event time and acquisition time are distinct. Never add a condition absent from the exact rules.'}


def features(span,rule):
    text=span['text'];lower=text.lower()
    return {'metric':any(t in lower for t in rule['metric_terms']),
            'date':bool(DATE.search(text)),
            'target_month':any(re.search(r'\b'+re.escape(m)+r'\b',lower) for m in rule['target_months']),
            'unit':bool(UNIT.search(text)), 'number':bool(NUMBER.search(text)),
            'release':bool(RELEASE.search(text)), 'action':bool(ACTION.search(text)),
            'table':bool(re.search(r'\|.*\||\t|\b(?:current|previous|total|quarter|year-on-year)\b',text,re.I)),
            'rule_source':any(span['url'].rstrip('/')==u.rstrip('/') for u in rule['rule_source_urls']),
            'declared_primary':span['url'] in rule['declared_primary_source_leads']}


def directed_select(packet,rule,state=None,reasons=(),limit=chain.FIRST_BYTES,registry=None):
    """Select original spans by rule coverage; retain all first-pass evidence."""
    state=copy.deepcopy(state) if state is not None else chain.initial_state(packet)
    state['rule_contract']=rule
    if 'Bind quoted values' not in state['instruction']:
        state['instruction']+=' Bind quoted values to the exact issuer/entity, metric, units, observation period and publication vintage. A newer observation is not the requested earlier release. Search snippets are discovery leads, not confirmed measurements. If evidence is missing, do not substitute absence for refutation.'
    state['context_omitted']=False
    if reasons:state['reading_targets']=list(reasons)
    if chain.request_bytes(state,registry)>limit:raise ValueError('Question and rule contract exceed fixed request bound')
    terms=rule['metric_terms'];span_features={s['evidence_id']:features(s,rule) for s in packet['evidence']}
    sources={s['source_id']:s for s in packet['sources']}
    # Low-frequency entity/metric terms help avoid generic navigation matches.
    frequency={t:sum(t in s['text'].lower() for s in packet['evidence']) for t in terms}
    def score(span):
        f=span_features[span['evidence_id']];lower=span['text'].lower()
        lexical=sum(1+math.log((1+len(packet['evidence']))/(1+frequency[t])) for t in terms if t in lower)
        value=lexical+3*f['target_month']+2*f['date']+2*f['unit']+2*(f['number'] and f['metric'])+2*f['table']
        value+=5*(f['rule_source'] and f['metric'])
        value+=5*f['declared_primary']+6*(f['declared_primary'] and span['start']==0)
        if rule['time_basis']=='release_window':value+=4*(f['release'] and (f['date'] or f['target_month']))
        if any(r in reasons for r in ('metric_definition','value_units','metric_coverage','unit_coverage')):value+=4*(f['number'] and (f['unit'] or f['metric']))
        if any('time' in r or 'date' in r or 'observation' in r for r in reasons):value+=4*(f['date'] or f['target_month'])
        if 'event_stage' in reasons:value+=4*f['action']
        if sources[span['source_id']].get('capture_metadata',{}).get('capture_method')=='tavily_basic_snippet_only':value-=6
        return value
    ranked=sorted(packet['evidence'],key=lambda s:(-score(s),s['source_id'],s['start']))
    kept={s['evidence_id'] for s in state['evidence']};present={s['source_id'] for s in state['sources']}
    selected_per_source={sid:sum(s['source_id']==sid for s in state['evidence']) for sid in sources}
    headers={}
    for span in packet['evidence']:headers.setdefault(span['source_id'],span)
    def add(span):
        if span['evidence_id'] in kept:return False
        fresh=span['source_id'] not in present
        state['evidence'].append(copy.deepcopy(span))
        if fresh:state['sources'].append({k:copy.deepcopy(sources[span['source_id']][k]) for k in
            ('source_id','url','body_sha256','capture_metadata','saved_body_truncated') if k in sources[span['source_id']]})
        if chain.request_bytes(state,registry)>limit:
            state['evidence'].pop()
            if fresh:state['sources'].pop()
            return False
        kept.add(span['evidence_id']);present.add(span['source_id']);selected_per_source[span['source_id']]+=1
        return True
    def include(span):
        header=headers[span['source_id']]
        cue=span_features[header['evidence_id']]
        # Bind a table/value to its source heading and vintage, including bilingual headers.
        if span['source_id'] not in present and (cue['metric'] or cue['date'] or cue['release']):add(header)
        if span['source_id'] not in present and (cue['metric'] or cue['date'] or cue['release']):return False
        return add(span)
    # Reserve part of the context for direct measurements before broad coverage.
    directed_ceiling=chain.request_bytes(state,registry)+(limit-chain.request_bytes(state,registry))*.60
    for span in ranked:
        if chain.request_bytes(state,registry)>=directed_ceiling:break
        if selected_per_source[span['source_id']]<3:include(span)
    # Add the strongest passage of each remaining source before filling neighbors.
    for span in ranked:
        if span['source_id'] not in present:include(span)
    index={s['evidence_id']:i for i,s in enumerate(packet['evidence'])}
    for span in ranked:
        include(span)
        i=index[span['evidence_id']]
        for neighbor in packet['evidence'][max(0,i-1):i+2]:
            if neighbor['source_id']==span['source_id']:add(neighbor)
    omitted=[s['evidence_id'] for s in packet['evidence'] if s['evidence_id'] not in kept]
    state['context_omitted']=bool(omitted)
    return state,{'selected_ids':sorted(kept),'omitted_ids':omitted,'state_sha256':digest(state),
                  'request_bytes':chain.request_bytes(state,registry),'request_byte_limit':limit,'routing_focus':list(reasons),
                  'selection':'Rule-directed original spans, measurement priority, source coverage and neighbors.',
                  'span_features':span_features}


def coverage(packet,state,rule,kind):
    """Observable coverage flags, not semantic fact verification or probability overrides."""
    seen={s['evidence_id'] for s in state['evidence']}
    all_features={s['evidence_id']:features(s,rule) for s in packet['evidence']}
    needs=['metric']
    if rule['date_mentions'] or rule['target_months']:needs.append('date')
    if rule['unit_mentions']:needs.append('unit')
    if kind in ('numeric','discrete'):needs.append('number')
    if rule['rule_source_urls']:needs.append('rule_source')
    missing=[n for n in needs if not any(all_features[i][n] for i in seen)]
    reasons=[n+'_coverage' for n in missing if any(f[n] for i,f in all_features.items() if i not in seen)]
    if rule['time_basis']=='release_window':
        # Audit publication vintage even if the model's sufficiency head is confident.
        candidates=[i for i,f in all_features.items() if i not in seen and f['date'] and (f['release'] or f['metric'])]
        if candidates:reasons.append('release_observation_distinction')
    return {'needed':needs,'missing_selected_indicators':missing,'program_reasons':reasons,
            'unresolved_in_saved_text':[n for n in missing if not any(f[n] for f in all_features.values())],
            'semantic_truth_verified':False,'missing_is_not_refuted':True}


def run_task(bundle,folder,*,row=None,metadata=None,dry_run=False):
    folder=Path(folder);kind=bundle['request'].get('question_type',bundle['request'].get('type','binary'))
    packet=balanced_packet(bundle)
    packet['question']=copy.deepcopy(bundle['request']);packet['question'].update(metadata or {})
    if row is not None:packet['question']['source_links']=copy.deepcopy(row.get('source_links',[]))
    if packet['question'].get('scaling'):packet['question']['scaling'].pop('continuous_range',None)
    rule=contract(packet['question']);spec=None if kind=='binary' else typed.spec(row)
    registry=chain.questions() if kind=='binary' else typed.questions(spec)
    for key in ('event_yes','event_outcome','time_window','observation_coverage','metric_definition','value_units'):
        if key in registry:
            registry[key]['instructions']+=' Apply state.rule_contract as quoted reading cues, not additional conditions. Distinguish release date from observation period, initial publication from revision, and exact entity/metric/units. Missing evidence is not refutation. Use exact original question rules when cues are incomplete.'
    identity={'protocol':PROTOCOL,'packet_sha256':digest(packet),'rule_sha256':digest(rule),'registry_sha256':digest(registry),
              'spec_sha256':digest(spec),'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'http_cap':2,'byte_limits':[chain.FIRST_BYTES,chain.SECOND_BYTES],'dry_run':dry_run}
    if (folder/'identity.json').exists() and load(folder/'identity.json')!=identity:raise ValueError('Frozen P0 analysis changed')
    save(folder/'identity.json',identity);save(folder/'packet.json',packet);save(folder/'rule-contract.json',rule)
    if spec:save(folder/'distribution-spec.json',spec)
    first,audit=directed_select(packet,rule,registry=registry);first_coverage=coverage(packet,first,rule,kind)
    save(folder/'first-state.json',first);save(folder/'first-input-audit.json',audit);save(folder/'first-coverage.json',first_coverage)
    if dry_run:return {'status':'prepared','coverage':first_coverage}
    response=chain.call(first,folder/'first',registry)
    router=chain.route if kind=='binary' else typed.route
    reasons=list(dict.fromkeys(router(response)+first_coverage['program_reasons']))
    second,second_audit=directed_select(packet,rule,first,reasons,chain.SECOND_BYTES,registry) if reasons else (first,audit)
    existing={s['evidence_id'] for s in first['evidence']}
    added=[s for s in second['evidence'] if s['evidence_id'] not in existing]
    gate={'reasons':reasons,'program_reasons':first_coverage['program_reasons'],
          'new_ids':[s['evidence_id'] for s in added],'new_chars':sum(len(s['text']) for s in added),
          'second_call_required':bool(reasons and added),'probability_override':False}
    save(folder/'routing.json',gate);final=response;second_error=None
    if gate['second_call_required']:
        save(folder/'second-state.json',second);save(folder/'second-input-audit.json',second_audit)
        save(folder/'second-coverage.json',coverage(packet,second,rule,kind))
        try:final=chain.call(second,folder/'second',registry)
        except RuntimeError as exc:
            second_error=str(exc);save(folder/'second-unavailable.json',{'error':second_error,'first_decision_retained':True})
    result={'status':'completed','protocol':PROTOCOL,'second_call_required':gate['second_call_required'],
            'routing':gate,'remaining_diagnostic_gaps':router(final),'coverage':first_coverage,
            'second_error':second_error,'no_forecasts_submitted':True,'no_retrieval_calls':True}
    if kind=='binary':
        p=final['answers']['event_yes']['noul']
        result.update(first_probability_yes=response['answers']['event_yes']['noul'],probability_yes=p,clipped_probability_yes=min(.98,max(.02,p)))
    else:result.update(type=kind,post_id=row['post_id'],first_forecast=typed.forecast(response,spec),forecast=typed.forecast(final,spec))
    save(folder/'result.json',result)
    return result
