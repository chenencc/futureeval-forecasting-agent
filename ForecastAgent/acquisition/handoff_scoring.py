"""Bounded Mercury scoring on paired immutable acquisition handoff states.

Only the frozen decision client may contact a provider. Labels are opened after
both routes are sealed, never while constructing model input. No submissions.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path

from ForecastAgent.acquisition import handoff
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.acquisition.pipeline import verify_baseline, reject_outcomes
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest, load, save, WARNING
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.providers import decisions

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT/'ForecastAgent/experiments/materials_handoff_score_protocol.json'
LABELS = ROOT/'ForecastAgent/experiments/materials_handoff_score_labels.json'


def text_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def protocol():
    verify_baseline()
    value = load(PROTOCOL)
    if decisions.MODEL != value['model'] or decisions.ENDPOINT != value['endpoint']:
        raise ValueError('Frozen decision model or endpoint changed')
    if text_sha(handoff.__file__) != value['handoff_implementation_sha256_lf']:
        raise ValueError('Frozen first-request packing changed')
    return value


def novel_chars(before, after):
    """Measure new original-coordinate characters, not newly named evidence IDs."""
    groups = {}
    for span in after['evidence']:
        groups.setdefault(handoff.space(span),[]).append((span['start'],span['end']))
    count = 0
    for key,spans in groups.items():
        covered = [(s['start'],s['end']) for s in before['evidence'] if handoff.space(s)==key]
        for start,end in handoff.ranges(spans):
            count += sum(b-a for a,b in handoff.uncovered(start,end,covered))
    return count


def extend(bundle, packet, first, reasons):
    # Keep the release diagnostic priorities and proposal selector. Compact the
    # candidate's normalized spans before measuring genuinely new original text.
    frontier=copy.deepcopy(packet)
    frontier['evidence']=[span for span in packet['evidence'] if handoff.uncovered(
        span['start'],span['end'],[(s['start'],s['end']) for s in first['evidence']
                                 if handoff.space(s)==handoff.space(span)])]
    # Covered E IDs may have become merged H IDs. Range identity, rather than
    # renamed IDs, keeps duplicates from consuming the conditional byte budget.
    proposed,audit = chain.select(frontier,first,reasons,chain.SECOND_BYTES)
    sources = {s['source_id']:s for s in packet['sources']}
    proposed['evidence'] = handoff.compact(proposed['evidence'],bundle['pages'],sources)
    if chain.request_bytes(proposed)>chain.SECOND_BYTES or audit_spans(bundle,proposed):
        raise ValueError('Conditional candidate input failed source/byte integrity')
    for span in first['evidence']:
        covered=[(s['start'],s['end']) for s in proposed['evidence'] if handoff.space(s)==handoff.space(span)]
        if handoff.uncovered(span['start'],span['end'],covered):
            raise ValueError('Conditional input removed first-pass original text')
    return proposed,{'release_proposal':audit,'request_bytes':chain.request_bytes(proposed),
                     'novel_coordinate_chars':novel_chars(first,proposed)}


def seal(folder, result):
    save(folder/'result.json',result)
    save(folder/'seal.json',{'result_sha256':digest(result),
        'response_sha256':{str(p.relative_to(folder)):digest(load(p)) for p in folder.glob('*/response.json')}})
    return result


def check_seal(folder):
    result=load(folder/'result.json');sealed=load(folder/'seal.json')
    if digest(result)!=sealed['result_sha256']:
        raise ValueError('Saved scoring result changed')
    for name,expected in sealed['response_sha256'].items():
        path=(folder/name).resolve()
        if not path.is_relative_to(folder.resolve()) or digest(load(path))!=expected:
            raise ValueError('Saved scoring response changed')
    return result


def run_route(bundle, folder, variant, frozen, bundle_file_sha):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    reject_outcomes(bundle['request'])
    packet=packet_for(bundle)
    if variant=='old_packing':first,packing=chain.select(packet)
    elif variant=='new_packing':first,packing=handoff.pack(bundle)
    else:raise ValueError('Unknown packing route')
    identity={'schema':'handoff-mercury-scoring-route-v1','question_id':str(bundle['request']['id']),
              'variant':variant,'bundle_file_sha256':bundle_file_sha,'bundle_sha256':digest(bundle),
              'first_state_sha256':digest(first),'packet_sha256':digest(packet),
              'registry_sha256':digest(chain.questions()),'protocol_sha256_lf':text_sha(PROTOCOL),
              'implementation_sha256_lf':text_sha(__file__),'model':decisions.MODEL,
              'limits':frozen['limits'],'routing':chain.ROUTING}
    if (folder/'identity.json').exists() and load(folder/'identity.json')!=identity:
        raise ValueError('Frozen scoring route changed; no silent restart')
    save(folder/'identity.json',identity)
    if (folder/'result.json').exists():return check_seal(folder)
    save(folder/'first-state.json',first);save(folder/'first-packing.json',packing)
    if audit_spans(bundle,first):raise ValueError('First request original evidence audit failed')
    result={'question_id':identity['question_id'],'variant':variant,'status':'failed',
            'first_probability_yes':None,'probability_yes':None,'no_forecasts_submitted':True,
            'input_identity_sha256':digest(identity),'evaluation_warning':WARNING}
    try:
        if not os.environ.get('OPENROUTER_API_KEY'):raise RuntimeError('Configured decision credential is unavailable')
        response=chain.call(first,folder/'first')
        result['first_probability_yes']=response['answers']['event_yes']['noul']
        final=response;reasons=chain.route(response)
        if reasons:
            if variant=='new_packing':second,second_audit=extend(bundle,packet,first,reasons)
            else:second,second_audit=chain.select(packet,first,reasons,chain.SECOND_BYTES)
        else:second,second_audit=first,packing
        new_chars=novel_chars(first,second)
        gate={'reasons':reasons,'new_original_chars':new_chars,'second_call_required':bool(reasons) and new_chars>=chain.ROUTING['minimum_new_chars']}
        save(folder/'routing.json',gate)
        result.update(routing=gate,selection='first_read',second_error=None)
        if gate['second_call_required']:
            save(folder/'second-state.json',second);save(folder/'second-packing.json',second_audit)
            try:
                final=chain.call(second,folder/'second');result['selection']='conditional_reread'
            except Exception as exc:
                result['second_error']=type(exc).__name__+': '+str(exc)
                save(folder/'second-unavailable.json',{'error':result['second_error'],'validated_first_retained':True})
        p=decisions.probability(final['answers']['event_yes']['noul'])
        result.update(status='completed_with_reread_failure' if result['second_error'] else 'completed',
                      probability_yes=p,clipped_probability_yes=min(.98,max(.02,p)),
                      first_clipped_probability_yes=min(.98,max(.02,result['first_probability_yes'])),
                      remaining_diagnostic_gaps=chain.route(final))
    except Exception as exc:
        result['error']=type(exc).__name__+': '+str(exc)
    return seal(folder,result)


def run_case(root, output, qid, *, dry_run=False):
    frozen=protocol()
    if qid not in frozen['question_ids']:raise ValueError('Question outside the frozen five')
    path=Path(root)/qid/frozen['collection_arm']/'bundle.json'
    raw=path.read_bytes();file_sha=hashlib.sha256(raw).hexdigest()
    if file_sha!=frozen['bundles'][qid]:raise ValueError('Frozen candidate bundle checksum changed')
    bundle=json.loads(raw);reject_outcomes(bundle['request'])
    if str(bundle['request']['id'])!=qid:raise ValueError('Question identity differs from frozen source')
    folder=Path(output)/qid
    rows={}
    for variant in frozen['route_order'][qid]:
        if dry_run:
            if variant=='old_packing':state,audit=chain.select(packet_for(bundle))
            else:state,audit=handoff.pack(bundle)
            rows[variant]={'status':'prepared','request_bytes':chain.request_bytes(state),
                           'state_sha256':digest(state),'integrity_errors':audit_spans(bundle,state)}
        else:rows[variant]=run_route(bundle,folder/variant,variant,frozen,file_sha)
    if path.read_bytes()!=raw:raise ValueError('Original source bundle changed during scoring')
    result={'question_id':qid,'bundle_file_sha256':file_sha,'routes':rows,
            'dry_run':dry_run,'new_source_or_search_calls':0,'submitted':False}
    if not dry_run:save(folder/'comparison.json',result)
    return result


def scores(p,y):
    raw=decisions.probability(p);clipped=min(.98,max(.02,raw))
    def loss(value):
        value=min(1-1e-9,max(1e-9,value))
        return -(y*math.log(value)+(1-y)*math.log(1-value))
    return {'raw_probability':raw,'clipped_probability':clipped,'raw_brier':(raw-y)**2,
            'clipped_brier':(clipped-y)**2,'raw_log_loss':loss(raw),
            'clipped_log_loss':loss(clipped),'correct_at_half':(raw>=.5)==bool(y)}


def review(output, source_root):
    frozen=protocol();root=Path(output)
    # Freeze/verify all model results and transport records BEFORE reading labels.
    rows=[];attempts=[];errors=[]
    for qid in frozen['question_ids']:
        source=Path(source_root)/qid/frozen['collection_arm']/'bundle.json'
        raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=frozen['bundles'][qid]:raise ValueError('Review original bundle changed')
        bundle=json.loads(raw);packet=packet_for(bundle)
        row={'question_id':qid,'routes':{}}
        for variant in ('old_packing','new_packing'):
            folder=root/qid/variant
            if not (folder/'result.json').exists():
                row['routes'][variant]={'status':'missing','probability_yes':None,'first_probability_yes':None}
                continue
            result=check_seal(folder)
            identity=load(folder/'identity.json')
            if identity['protocol_sha256_lf']!=text_sha(PROTOCOL) or identity['bundle_file_sha256']!=frozen['bundles'][qid]:
                raise ValueError('Review input identity mismatch')
            expected=chain.select(packet)[0] if variant=='old_packing' else handoff.pack(bundle)[0]
            if digest(expected)!=identity['first_state_sha256'] or digest(load(folder/'first-state.json'))!=digest(expected):
                raise ValueError('First request differs from reproducible frozen source packing')
            for saved in folder.glob('*-state.json'):
                if audit_spans(bundle,load(saved)):raise ValueError('Actual analyst input failed original-span verification')
            for stage,limit in (('first',chain.FIRST_BYTES),('second',chain.SECOND_BYTES)):
                records=list((folder/stage/'http').glob('*.json'))
                if len(records)>1:errors.append({'question_id':qid,'variant':variant,'stage':stage,'reason':'HTTP cap exceeded'})
                for p in records:
                    record=load(p);request=record.get('request',{})
                    if request.get('model')!=decisions.MODEL or record.get('endpoint')!=decisions.ENDPOINT:
                        errors.append({'question_id':qid,'variant':variant,'reason':'Unexpected provider route'})
                    if len(json.dumps(request).encode())>limit:
                        errors.append({'question_id':qid,'variant':variant,'reason':'Request byte limit exceeded'})
                    if digest(request.get('questions'))!=identity['registry_sha256']:
                        errors.append({'question_id':qid,'variant':variant,'reason':'Decision registry changed'})
                    if digest(request.get('state'))!=digest(load(folder/(stage+'-state.json'))):
                        errors.append({'question_id':qid,'variant':variant,'reason':'Actual request differs from saved state'})
                    if stage=='first' and digest(request.get('state'))!=identity['first_state_sha256']:
                        errors.append({'question_id':qid,'variant':variant,'reason':'Actual first request differs from frozen identity'})
                    attempts.append({'question_id':qid,'variant':variant,'stage':stage,
                                     'record':str(p.relative_to(root)),'record_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                                     'status':record.get('status'),'http_status':record.get('http_status'),
                                     'request_bytes':len(json.dumps(request).encode()),
                                     'usage':record.get('response',{}).get('usage')})
                rp=folder/stage/'response.json'
                if rp.exists():
                    response=decisions.validate(load(rp),chain.questions())
                    received=[load(p) for p in records if load(p).get('status')=='received']
                    if len(received)!=1 or digest(received[0].get('response'))!=digest(response):
                        errors.append({'question_id':qid,'variant':variant,'reason':'Response differs from transport journal'})
                    if stage=='first' and response['answers']['event_yes']['noul']!=result['first_probability_yes']:
                        errors.append({'question_id':qid,'variant':variant,'reason':'First score differs from raw response'})
                    if stage=='second' and result.get('selection')=='conditional_reread' and response['answers']['event_yes']['noul']!=result['probability_yes']:
                        errors.append({'question_id':qid,'variant':variant,'reason':'Final score differs from raw response'})
            row['routes'][variant]=result
        rows.append(row)
    if text_sha(LABELS)!=frozen['labels_sha256_lf']:raise ValueError('Frozen evaluation labels changed')
    labels=load(LABELS)
    for row in rows:
        label=labels['records'][row['question_id']]
        row['resolution']=label['resolution'];row['label_provenance']=label
        for variant,result in row['routes'].items():
            for stage,key in (('first','first_probability_yes'),('final','probability_yes')):
                if result.get(key) is not None:result[stage+'_scores']=scores(result[key],label['resolution'])
    metrics={}
    for stage in ('first','final'):
        paired=[r for r in rows if all(stage+'_scores' in r['routes'][a] for a in ('old_packing','new_packing'))]
        metrics[stage]={'requested_pairs':len(rows),'scored_pairs':len(paired),'routes':{}}
        for variant in ('old_packing','new_packing'):
            values=[r['routes'][variant][stage+'_scores'] for r in paired]
            metrics[stage]['routes'][variant]={key:sum(v[key] for v in values)/len(values) if values else None
                for key in ('raw_brier','clipped_brier','raw_log_loss','clipped_log_loss','correct_at_half')}
        if paired:
            metrics[stage]['new_minus_old_clipped_brier']=metrics[stage]['routes']['new_packing']['clipped_brier']-metrics[stage]['routes']['old_packing']['clipped_brier']
    known=unknown=0
    for attempt in attempts:
        usage=attempt['usage'] or {}
        total=usage.get('total_tokens')
        if type(total) is int:known+=total
        elif type(usage.get('input_tokens')) is int and type(usage.get('output_tokens')) is int:
            known+=usage['input_tokens']+usage['output_tokens']
        else:unknown+=1
    if len(attempts)>frozen['limits']['campaign_maximum_http']:errors.append({'reason':'Campaign HTTP cap exceeded'})
    observed={a['record'] for a in attempts}
    extra=[str(p.relative_to(root)) for p in root.glob('**/http/*.json') if str(p.relative_to(root)) not in observed]
    if extra:errors.append({'reason':'Unexpected provider journals outside the frozen routes','records':extra})
    return {'schema':'material-handoff-mercury-score-review-v1','protocol':frozen,'rows':rows,'metrics':metrics,
            'attempts':attempts,'consumption':{'actual_mercury_http_attempts':len(attempts),
                'known_reported_tokens':known,'attempts_with_unknown_usage':unknown,
                'search_calls':0,'source_fetch_calls':0,'new_supplement_calls':0},
            'integrity_errors':errors,'labels_sha256_lf':text_sha(LABELS),'source_labels_provenance':labels,
            'complete':all(r['routes'][a].get('probability_yes') is not None for r in rows for a in ('old_packing','new_packing')),
            'promotion_allowed':False,'evaluation_warning':WARNING,
            'limits':'Five known development cases, four No and one Yes; no cutoff enforcement, no fitted calibration, no generalization claim.',
            'submitted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--question-id')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--review',type=Path)
    args=parser.parse_args()
    if args.review:
        if args.root is None:raise ValueError('Offline review requires the original frozen source root')
        report=review(args.output,args.root);save(args.review,report)
        print(json.dumps({'complete':report['complete'],'metrics':report['metrics'],
                          'consumption':report['consumption'],'integrity_errors':report['integrity_errors']}))
    else:
        result=run_case(args.root,args.output,args.question_id,dry_run=args.dry_run)
        print(json.dumps({'question_id':result['question_id'],'dry_run':result['dry_run'],
                          'routes':{k:{f:v.get(f) for f in ('status','first_probability_yes','probability_yes','request_bytes')} for k,v in result['routes'].items()}}))
        if not args.dry_run and any(v.get('probability_yes') is None for v in result['routes'].values()):
            raise RuntimeError('Failed decision preserved; no automatic budget reset')


if __name__=='__main__':
    main()
