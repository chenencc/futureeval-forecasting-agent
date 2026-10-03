"""Release 1.0.1 live original-evidence decisions with conditional rereading."""
import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import range_metadata, grid, payload
from ForecastAgent.analysis.pilot import load, save, digest

VERSION = '1.0.1'
PROTOCOL = 'official-mercury-original-evidence-1.0.1'
LIVE_WARNING = 'Live automatic competition forecast; no resolution labels or community probabilities supplied.'


def distribution_spec(request):
    """Use authoritative platform bounds, including UTC date scales."""
    kind = request['question_type']
    if kind == 'multiple_choice':
        options = request['options']
        if not isinstance(options,list) or len(options)<2 or len(set(options))!=len(options):
            raise ValueError('Exact platform options required')
        return {'kind':kind,'options':options,'criteria':{f'option_{i}':o for i,o in enumerate(options)}}
    meta = range_metadata(request); locations = grid(meta); count = meta['inbound_outcome_count']
    edges = [locations[i] for i in sorted({round(count*i/20) for i in range(21)})]
    def display(value):
        return datetime.fromtimestamp(value,timezone.utc).isoformat() if kind=='date' else format(value,'.12g')
    criteria = {}
    if meta['open_lower_bound']:criteria['below']='Resolving value strictly below '+display(edges[0])+'.'
    for i,(a,b) in enumerate(zip(edges,edges[1:])):
        end = 'less than or equal to' if i==len(edges)-2 and not meta['open_upper_bound'] else 'strictly less than'
        criteria[f'bin_{i}']=f'Resolving value at least {display(a)} and {end} {display(b)} in the question units.'
    if meta['open_upper_bound']:criteria['above']='Resolving value at or above '+display(edges[-1])+', including a beyond-range outcome under the exact resolution rules.'
    return {'kind':kind,'edges':edges,'meta':meta,'criteria':criteria,'official_metadata_available':True,
            'interpolation_policy':'Uniform mass in each platform-grid bin; no additional model calls.'}


def packet_for(bundle):
    visible = copy.deepcopy(bundle)
    visible['gaps'] = list(bundle.get('gaps',bundle.get('result',{}).get('gaps',[])))
    if visible.get('supplement_lineage'):
        visible['gaps'].append({'supplement_remaining_gap_count':visible['supplement_lineage'].get('remaining_gap_count')})
    packet = chain.full_packet(visible)
    # Keep exact rules, fine print, background, units, time windows and API scale.
    packet['question'] = copy.deepcopy(bundle['request'])
    if packet['question'].get('scaling'):
        packet['question']['scaling'].pop('continuous_range',None)
    packet['evaluation_warning'] = LIVE_WARNING
    return packet


def run(bundle, folder, *, reading_hints=None):
    folder=Path(folder); packet=packet_for(bundle); question=bundle['request'];kind=question['question_type']
    spec=None if kind=='binary' else distribution_spec(question)
    registry=chain.questions() if kind=='binary' else typed.questions(spec)
    selector = chain.select
    if reading_hints is not None:
        from ForecastAgent.analysis import priority_reading
        # Live packet changes the warning/question envelope, but exact source
        # spans must still match the supplement's immutable saved packet.
        original_packet = chain.full_packet(bundle)
        if reading_hints['packet_sha256'] != digest(original_packet):
            raise ValueError('Supplement reading hints changed')
        reading_hints = copy.deepcopy(reading_hints)
        reading_hints['packet_sha256'] = digest(packet)
        def selector(packet, state=None, reasons=(), limit=chain.FIRST_BYTES, decision_questions=None):
            return priority_reading.select(packet, reading_hints, state, reasons, limit, decision_questions)
    # Validate distribution feasibility before any HTTP reservation.
    if kind=='multiple_choice':payload(question,{'probability_yes_per_category':{o:1/len(question['options']) for o in question['options']}})
    elif kind!='binary':
        count=spec['meta']['inbound_outcome_count'];payload(question,{'continuous_cdf':[i/count for i in range(count+1)]})
    identity={'protocol':PROTOCOL,'packet_sha256':digest(packet),'spec_sha256':digest(spec),
              'questions_sha256':digest(registry),'http_cap':2,'byte_limits':[chain.FIRST_BYTES,chain.SECOND_BYTES],
              'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'selector_sha256':hashlib.sha256(Path(chain.__file__).read_bytes()).hexdigest()}
    if reading_hints is not None:
        identity['candidate_reading_hints_sha256'] = digest(reading_hints)
        identity['candidate_selector_sha256'] = hashlib.sha256(Path(priority_reading.__file__).read_bytes()).hexdigest()
    if (folder/'identity.json').exists() and load(folder/'identity.json')!=identity:
        raise ValueError('Frozen live Mercury identity changed')
    save(folder/'identity.json',identity);save(folder/'packet.json',packet);save(folder/'distribution-spec.json',spec)
    first=chain.initial_state(packet)
    first['evaluation_warning']=LIVE_WARNING
    first['instruction'] += ' Forecast the eventual resolution of this currently open question. All listed conditions are diagnostic, not a substitute for the event probability.'
    # Reserve live instructions before selecting original evidence spans.
    first,audit=selector(packet,first,limit=chain.FIRST_BYTES,decision_questions=registry)
    save(folder/'first-state.json',first);save(folder/'first-input-audit.json',audit)
    response=chain.call(first,folder/'first',registry)
    router=chain.route if kind=='binary' else typed.route
    reasons=router(response)
    second,second_audit=selector(packet,first,reasons,chain.SECOND_BYTES,registry) if reasons else (first,audit)
    existing={s['evidence_id'] for s in first['evidence']}
    added=[s for s in second['evidence'] if s['evidence_id'] not in existing]
    new_chars=sum(len(s['text']) for s in added)
    gate={'reasons':reasons,'new_ids':[s['evidence_id'] for s in added],'new_chars':new_chars,
          'second_call_required':bool(reasons) and new_chars>=chain.ROUTING['minimum_new_chars'],
          'diagnostics_are_not_facts':True}
    save(folder/'routing.json',gate)
    final=response;selection='mercury_first_read';second_error=None
    if gate['second_call_required']:
        save(folder/'second-state.json',second);save(folder/'second-input-audit.json',second_audit)
        try:
            final=chain.call(second,folder/'second',registry);selection='mercury_conditional_reread'
        except RuntimeError as exc:
            # Keep the already validated first decision; never invent a default.
            second_error=str(exc);save(folder/'second-unavailable.json',{'error':second_error,'first_decision_retained':True})
    if kind=='binary':raw={'probability_yes':final['answers']['event_yes']['noul']}
    else:
        forecast=typed.forecast(final,spec)
        raw={'probability_yes_per_category':forecast['probabilities']} if kind=='multiple_choice' else {'continuous_cdf':forecast['raw_cdf']}
    candidate=payload(question,raw)
    result={'protocol':PROTOCOL,'release_version':VERSION,'status':'completed','payload':candidate,
            'selection':selection,'routing':gate,'remaining_diagnostic_gaps':router(final),
            'second_error':second_error,'evaluation_warning':LIVE_WARNING,'no_forecasts_submitted':True}
    save(folder/'result.json',result)
    # Publish only automatic decision diagnostics and exact original citations.
    state=second if selection=='mercury_conditional_reread' else first
    comment=f'# ForecastAgent {VERSION}\n\nOriginal acquisition → independent supplement → Mercury original-evidence conditional rereading.\n\n'
    comment+='## Resolution rules\n'+question['resolution_criteria']+'\n\n'+question.get('fine_print','')
    comment+='\n\n## Decision diagnostics\n'+__import__('json').dumps({'selection':selection,'remaining_gaps':result['remaining_diagnostic_gaps'],'answers':final['answers']},ensure_ascii=False)
    comment+='\n\n## Saved original evidence\n'+'\n\n'.join(f"[{s['evidence_id']}] {s['url']} (characters {s['start']}–{s['end']})\n{s['text']}" for s in state['evidence'])
    return {'payload':candidate,'selection':selection,'release_version':VERSION,
            'model_result_sha256':digest(result),'comment':comment,'automatic':True}
