"""Context delivery to the frozen Mercury registry, prompts and payload policy."""
import copy
import hashlib
import json
from pathlib import Path

from ForecastAgent.acquisition import context_delivery as delivery, handoff
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.analysis import mercury_evidence_chain as chain, mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.competition import live
from ForecastAgent.competition.mercury import distribution_spec, packet_for, LIVE_WARNING

VERSION = '1.0.4'
PROTOCOL = 'official-mercury-context-delivery-1.0.4'


def preserved(first, second):
    return not any(handoff.uncovered(s['start'],s['end'],[(e['start'],e['end'])
        for e in second['evidence'] if handoff.space(e)==handoff.space(s)]) for s in first['evidence'])


def check(bundle,state,registry,limit,original):
    if audit_spans(bundle,state) or not preserved(original,state):
        raise ValueError('Evidence coordinates or old-visible material changed')
    if state['question']!=original['question'] or state['instruction']!=original['instruction']:
        raise ValueError('Frozen question or forecast instructions changed')
    if chain.request_bytes(state,registry)>limit:
        raise ValueError('Evidence request exceeds its registry-aware byte cap')


def select_first(bundle,registry):
    baseline,base_audit=delivery.baseline(bundle,registry)
    check(bundle,baseline,registry,chain.FIRST_BYTES,baseline)
    try:
        state,audit=delivery.pack(bundle,registry)
        check(bundle,state,registry,chain.FIRST_BYTES,baseline)
        return state,{'mode':'complete_context','packing':audit}
    except Exception as exc:
        return baseline,{'mode':'release_selector_fallback','baseline':base_audit,
            'packing_error':type(exc).__name__+': '+str(exc),'new_provider_attempts':0}


def select_second(bundle,first,reasons,registry):
    try:
        state,audit=delivery.extend(bundle,first,reasons,registry)
        check(bundle,state,registry,chain.SECOND_BYTES,first)
        return state,{'mode':'complete_context','packing':audit}
    except Exception as exc:
        error=type(exc).__name__+': '+str(exc)
    try:
        state,audit=chain.select(packet_for(bundle),first,reasons,chain.SECOND_BYTES,registry)
        check(bundle,state,registry,chain.SECOND_BYTES,first)
        return state,{'mode':'release_selector_fallback','baseline':audit,'packing_error':error,
                      'new_provider_attempts':0}
    except Exception as exc:
        return copy.deepcopy(first),{'mode':'first_read_preserved','packing_error':error,
            'fallback_error':type(exc).__name__+': '+str(exc),'new_provider_attempts':0}


def run(bundle,folder):
    folder=Path(folder);packet=packet_for(bundle);question=bundle['request'];kind=question['question_type']
    spec=None if kind=='binary' else distribution_spec(question)
    registry=chain.questions() if kind=='binary' else typed.questions(spec)
    if kind=='multiple_choice':
        payload(question,{'probability_yes_per_category':{o:1/len(question['options']) for o in question['options']}})
    elif kind!='binary':
        count=spec['meta']['inbound_outcome_count']
        payload(question,{'continuous_cdf':[i/count for i in range(count+1)]})
    identity={'protocol':PROTOCOL,'packet_sha256':digest(packet),'spec_sha256':digest(spec),
        'questions_sha256':digest(registry),'http_cap':2,'byte_limits':[chain.FIRST_BYTES,chain.SECOND_BYTES],
        'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'selector_sha256':hashlib.sha256(Path(chain.__file__).read_bytes()).hexdigest(),
        'delivery_sha256_lf':hashlib.sha256(Path(delivery.__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest()}
    if (folder/'identity.json').exists() and load(folder/'identity.json')!=identity:
        raise ValueError('Frozen live analysis or evidence-delivery identity changed')
    save(folder/'identity.json',identity);save(folder/'packet.json',packet);save(folder/'distribution-spec.json',spec)
    first,audit=select_first(bundle,registry)
    save(folder/'first-state.json',first);save(folder/'first-input-audit.json',audit)
    response=chain.call(first,folder/'first',registry)
    router=chain.route if kind=='binary' else typed.route
    reasons=router(response)
    second,second_audit=select_second(bundle,first,reasons,registry) if reasons else (first,audit)
    new_chars=delivery.novel_chars(first,second)
    gate={'reasons':reasons,'new_coordinate_chars':new_chars,
        'second_call_required':bool(reasons) and new_chars>=chain.ROUTING['minimum_new_chars'],
        'diagnostics_are_not_facts':True}
    save(folder/'routing.json',gate)
    final=response;selection='mercury_first_read';second_error=None
    if gate['second_call_required']:
        save(folder/'second-state.json',second);save(folder/'second-input-audit.json',second_audit)
        try:
            final=chain.call(second,folder/'second',registry);selection='mercury_conditional_reread'
        except RuntimeError as exc:
            second_error=str(exc)
            save(folder/'second-unavailable.json',{'error':second_error,'first_decision_retained':True})
    if kind=='binary':
        raw={'probability_yes':final['answers']['event_yes']['noul']}
    else:
        forecast=typed.forecast(final,spec)
        raw={'probability_yes_per_category':forecast['probabilities']} if kind=='multiple_choice' else {'continuous_cdf':forecast['raw_cdf']}
    candidate=payload(question,raw)
    result={'protocol':PROTOCOL,'release_version':VERSION,'status':'completed','payload':candidate,
        'selection':selection,'routing':gate,'remaining_diagnostic_gaps':router(final),
        'second_error':second_error,'evaluation_warning':LIVE_WARNING,'no_forecasts_submitted':True,
        'delivery_diagnostics':{'first':audit,'second':second_audit if reasons else None}}
    save(folder/'result.json',result)
    state=second if selection=='mercury_conditional_reread' else first
    sources={s['source_id']:s for s in state['sources']}
    comment=f'# ForecastAgent {VERSION}\n\nOriginal acquisition → independent supplement → Mercury original-evidence conditional rereading.\n\n'
    comment+='## Resolution rules\n'+question['resolution_criteria']+'\n\n'+question.get('fine_print','')
    comment+='\n\n## Decision diagnostics\n'+json.dumps({'selection':selection,'remaining_gaps':result['remaining_diagnostic_gaps'],'answers':final['answers']},ensure_ascii=False)
    comment+='\n\n## Saved original evidence\n'+'\n\n'.join(
        f"[{s['evidence_id']}] {sources[s['source_id']]['url']} (characters {s['start']}–{s['end']})\n{s['text']}" for s in state['evidence'])
    return {'payload':candidate,'selection':selection,'release_version':VERSION,
            'model_result_sha256':digest(result),'comment':comment,'automatic':True}


def analyze(bundle_path,folder,ident):
    """Keep the previously authorized single reasoning route on service failure."""
    bundle=load(bundle_path)
    if bundle['request']['id']!=str(ident):
        raise ValueError('Analysis question identity mismatch')
    try:
        candidate=run(bundle,folder/'mercury-v1.0.1')
    except RuntimeError as exc:
        save(folder/'mercury-unavailable.json',{'release_version':VERSION,'error':str(exc),
            'fallback':'single_available_reasoning_route','no_budget_reset':True})
        candidate=live.legacy_analyze(bundle_path,folder/'reasoning-fallback-v1.0.1',ident,reasoning_only=True)
        candidate['release_version']=VERSION
        candidate['selection']='single_available_reasoning_route'
        candidate['comment']=candidate['comment'].replace('# ForecastAgent 1.0 competition',f'# ForecastAgent {VERSION} competition')
        candidate['comment']+='\n\nMercury was unavailable; the previously authorized reasoning-only fallback supplied this forecast.'
    save(folder/'candidate.json',candidate)
    return candidate
