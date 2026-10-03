"""Condition judgments with exactly the historical final request's evidence coverage."""
import copy
import hashlib
from pathlib import Path
from ForecastAgent.analysis import condition_chain as cc, mercury_evidence_chain as chain
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.providers import decisions

PROTOCOL = 'mercury-matched-original-coverage-v1'
# This is a transport bound, NOT a token count or a model context guarantee.
# It must not shrink the frozen evidence. Provider context errors remain explicit failures.
REQUEST_BYTES = 96000


def final_request(folder):
    """Use the last successful request actually used to produce the historical forecast."""
    folder = Path(folder)
    stage = 'second' if (folder/'second/response.json').exists() else 'first'
    return load(folder/stage/'request.json')


def coverage_identity(state):
    return digest({key:state.get(key) for key in ('question','evidence','sources')})


def condition_questions(state):
    questions, bindings = cc.registry(state)
    # Compact repeated option descriptions without omitting any passage or polarity.
    for question in questions.values():
        question['instructions'] += ' Option IDs map to exact `evidence_id` values in `evidence`; evaluate that passage with its source context.'
        for option in question['criteria']:
            if option.startswith(('supports_', 'refutes_')):
                polarity, evidence_id = option.split('_', 1)
                question['criteria'][option] = f'Passage {evidence_id} directly {polarity} this condition.'
    return questions, bindings


def run_task(bundle, folder, frozen_request, *, row=None, dry_run=False):
    folder=Path(folder);state=copy.deepcopy(frozen_request['state'])
    questions, bindings=condition_questions(state)
    event_key='event_yes' if 'event_yes' in frozen_request['questions'] else 'event_outcome'
    final_questions={event_key:copy.deepcopy(frozen_request['questions'][event_key])}
    final_questions[event_key]['instructions'] += ' Also consider `condition_bindings`, which are uncertain model assessments tied to original passages, not verified facts. Passage-selection probabilities are not independent condition truth probabilities. Apply the exact original AND/OR/exception rules; do not multiply these probabilities. Missing evidence is not refutation.'
    identity={'protocol':PROTOCOL,'frozen_request_sha256':digest(frozen_request),
              'bundle_sha256':digest(bundle),'coverage_sha256':coverage_identity(state),
              'condition_questions_sha256':digest(questions),'final_questions_sha256':digest(final_questions),
              'http_cap':2,'request_byte_bound':REQUEST_BYTES,'dry_run':dry_run,
              'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if (folder/'identity.json').exists() and load(folder/'identity.json')!=identity:
        raise ValueError('Frozen matched coverage experiment changed')
    save(folder/'identity.json',identity);save(folder/'baseline-final-request.json',frozen_request)
    packet=chain.full_packet(bundle);packet['question']=copy.deepcopy(state['question'])
    save(folder/'packet.json',packet);save(folder/'first-state.json',state)
    save(folder/'condition-questions.json',questions);save(folder/'question-bindings.json',bindings)
    # Exact offsets and hashes are checked before either network request.
    mock={'model':decisions.MODEL,'answers':{k:{'type':'choice','choice':'insufficient','confidence':1.,
          'probabilities':{option:float(option=='insufficient') for option in q['criteria']}}
          for k,q in questions.items()}}
    synthetic=cc.bind(state,mock,questions,bindings,bundle)
    first_bytes=chain.request_bytes(state,questions)
    estimated_final_bytes=chain.request_bytes({**state,'condition_bindings':synthetic},final_questions)+4000
    if max(first_bytes,estimated_final_bytes)>REQUEST_BYTES:
        raise ValueError('Full frozen evidence exceeds transport bound; no text removed and no inference attempted')
    audit={'coverage_sha256':identity['coverage_sha256'],'original_passages':len(state['evidence']),
           'original_chars':sum(len(s['text']) for s in state['evidence']),
           'first_request_bytes':first_bytes,'estimated_final_request_bytes':estimated_final_bytes,
           'evidence_removed':0,'evidence_added':0,'sources_changed':False,'question_changed':False,
           'token_count_estimate':None,'context_limit_policy':'Provider enforces its token limit; context errors are preserved, never silently truncate.'}
    save(folder/'matched-coverage-audit.json',audit)
    spec=typed.spec(row) if event_key=='event_outcome' else None
    if spec:save(folder/'distribution-spec.json',spec)
    if dry_run:return {'status':'prepared','protocol':PROTOCOL,'coverage':audit}
    first=chain.call(state,folder/'first',questions)
    receipts=cc.bind(state,first,questions,bindings,bundle)
    save(folder/'condition-bindings.json',receipts)
    final_state={**copy.deepcopy(state),'condition_bindings':receipts}
    if coverage_identity(final_state)!=identity['coverage_sha256']:raise ValueError('Frozen original coverage changed')
    if chain.request_bytes(final_state,final_questions)>REQUEST_BYTES:
        raise ValueError('Final request bound exceeded; condition records preserved, no text removed')
    save(folder/'second-state.json',final_state)
    final=chain.call(final_state,folder/'second',final_questions)
    result={'status':'completed','protocol':PROTOCOL,'second_call_required':True,
            'coverage':audit,'provenance_verified':True,'semantic_truth_verified':False,
            'no_retrieval_calls':True,'no_forecasts_submitted':True}
    if event_key=='event_yes':
        p=final['answers'][event_key]['noul'];result.update(probability_yes=p,clipped_probability_yes=min(.98,max(.02,p)))
    else:result.update(type=row['type'],post_id=row['post_id'],forecast=typed.forecast(final,spec))
    save(folder/'result.json',result)
    return result
