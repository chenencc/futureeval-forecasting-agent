"""Candidate bounded selector consuming integrity-checked repair reading hints."""
import copy
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest


def select(packet, hints, state=None, reasons=(), limit=chain.FIRST_BYTES, decision_questions=None):
    if hints['packet_sha256'] != digest(packet):
        raise ValueError('Reading hints do not match saved packet')
    state = copy.deepcopy(state) if state is not None else chain.initial_state(packet)
    spans = {r['evidence_id']: r for r in packet['evidence']}
    sources = {s['source_id']: {k: copy.deepcopy(s[k]) for k in
               ('source_id', 'url', 'body_sha256', 'capture_metadata', 'saved_body_truncated') if k in s}
               for s in packet['sources']}
    kept = {s['evidence_id'] for s in state['evidence']}; omitted = []
    if chain.request_bytes(state, decision_questions) > limit:
        raise ValueError('Existing evidence exceeds byte limit')
    # Priority passages receive up to half the available window, leaving breadth.
    ceiling = chain.request_bytes(state, decision_questions) + max(0, limit-chain.request_bytes(state, decision_questions))//2
    for hint in hints['spans']:
        span = spans.get(hint['evidence_id'])
        if not span or any(span[k] != hint[k] for k in ('url', 'body_sha256', 'start', 'end')):
            raise ValueError('Reading hint provenance changed')
        if span['evidence_id'] in kept: continue
        candidate = copy.deepcopy(state); candidate['evidence'].append(copy.deepcopy(span))
        if span['source_id'] not in {s['source_id'] for s in candidate['sources']}:
            candidate['sources'].append(sources[span['source_id']])
        if chain.request_bytes(candidate, decision_questions) <= ceiling:
            state = candidate; kept.add(span['evidence_id'])
        else: omitted.append(span['evidence_id'])
    result, audit = chain.select(packet, state, reasons, limit, decision_questions)
    selected = set(audit['selected_ids'])
    audit.update(priority_hints_sha256=digest(hints), priority_selected_ids=[h['evidence_id'] for h in hints['spans'] if h['evidence_id'] in selected],
                 priority_omitted_ids=[h['evidence_id'] for h in hints['spans'] if h['evidence_id'] not in selected])
    return result, audit
