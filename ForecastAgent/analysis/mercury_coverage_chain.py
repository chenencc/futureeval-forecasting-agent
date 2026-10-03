"""Opt-in coverage-aware Mercury replay; production and baseline stay frozen."""
import copy
import hashlib
import math
import re
from pathlib import Path

from ForecastAgent.analysis import mercury_evidence_chain as baseline
from ForecastAgent.analysis.pilot import digest, load, save

PROTOCOL = 'mercury-coverage-chain-v1'
STOP = set('will this question resolve resolves resolution criteria before after according official country event evidence state primarily during under from with that have been their into than other when must only all and the for not any yes no'.split())


def questions():
    registry = baseline.questions()
    registry['event_yes']['instructions'] += (
        ' Forecast the event probability, not the probability that the supplied excerpts prove it. '
        'Distinguish an unobserved event from a refuted event. An earlier nonoccurrence, an unrelated '
        'chart or a different observation month does not establish NO for the full required window. '
        'For an interval still unobserved, consider the remaining opportunity and supplied context. '
        'Do not invent later facts or automatically assign 0.5 for missing evidence. '
        'A peak after the deadline does not establish a qualifying peak before the deadline.'
    )
    for key in baseline.CHECKS:
        registry[key]['criteria']['contradicted'] = (
            'Direct evidence refutes the condition for the required scope and observation window. '
            'A snapshot before the deadline or a missing measurement is insufficient instead.'
        )
    return registry


def select(packet, state=None, reasons=(), limit=baseline.FIRST_BYTES, decision_questions=None):
    """Rank exact original spans; deduplicate copies without claiming independence."""
    registry = decision_questions or questions()
    state = copy.deepcopy(state) if state is not None else baseline.initial_state(packet)
    if state is not None and 'Source counts and collection gaps' not in state['instruction']:
        state['instruction'] += ' Source counts and collection gaps describe retrieval, not event outcomes.'
    question = packet['question']
    query = question.get('question', '') + ' ' + question.get('resolution_criteria', '')
    # Markdown URLs are provenance, not lexical query terms.
    query = re.sub(r'https?://[^\s)]+', '', query).lower()
    terms = set(re.findall(r'\b[a-z][a-z0-9]{2,}\b', query)) - STOP
    phrases = re.findall(r'["“]([^"”]{3,80})["”]', query)
    spans = packet['evidence']
    frequency = {t: sum(t in s['text'].lower() for s in spans) for t in terms}

    def rank(span):
        # Ignore link destinations for ranking, but retain unmodified original text.
        text = re.sub(r'https?://[^\s)>]+', '', span['text']).lower()
        score = sum(math.log(1 + len(spans) / (1 + frequency[t])) for t in terms if re.search(r'\b' + re.escape(t) + r'\b', text))
        score += 5 * sum(phrase in text for phrase in phrases)
        # Original table rows or measured observations with an entity anchor.
        if score and re.search(r'\d', text) and ('|' in text or '%' in text):
            score += 2
        return (-score, span['source_id'], span['start'])

    ranked = sorted(spans, key=rank)
    groups = {}
    for span in ranked:
        groups.setdefault(span['source_id'], []).append(span)
    # One relevant passage per source, followed by global relevance. Headers have
    # no automatic priority; identical content is not repeated across captures.
    order = [items[0] for items in groups.values()] + ranked
    source_map = {s['source_id']: {k: copy.deepcopy(s[k]) for k in
                  ('source_id', 'url', 'body_sha256', 'capture_metadata', 'saved_body_truncated') if k in s}
                  for s in packet['sources']}
    kept = {s['evidence_id'] for s in state['evidence']}
    text_hashes = {digest(re.sub(r'\s+', ' ', s['text']).strip()) for s in state['evidence']}
    duplicates = []
    if baseline.request_bytes(state, registry) > limit:
        raise ValueError('Existing state exceeds decision byte bound')
    for span in order:
        if span['evidence_id'] in kept:
            continue
        fingerprint = digest(re.sub(r'\s+', ' ', span['text']).strip())
        if fingerprint in text_hashes:
            duplicates.append(span['evidence_id'])
            continue
        fresh = span['source_id'] not in {s['source_id'] for s in state['sources']}
        state['evidence'].append(copy.deepcopy(span))
        if fresh:
            state['sources'].append(source_map[span['source_id']])
        if baseline.request_bytes(state, registry) > limit:
            state['evidence'].pop()
            if fresh:
                state['sources'].pop()
        else:
            kept.add(span['evidence_id'])
            text_hashes.add(fingerprint)
    omitted = [s['evidence_id'] for s in spans if s['evidence_id'] not in kept]
    state['context_omitted'] = bool(omitted)
    return state, {'selected_ids': sorted(kept), 'omitted_ids': omitted,
                   'duplicate_span_ids': sorted(set(duplicates)),
                   'state_sha256': digest(state), 'request_bytes': baseline.request_bytes(state, registry),
                   'request_byte_limit': limit, 'routing_focus': list(reasons),
                   'selection': 'Rule and entity relevance, inverse frequency, exact original spans; no header priority.'}


def run_task(bundle, folder, metadata=None, dry_run=False):
    folder = Path(folder)
    packet = baseline.full_packet(bundle)
    packet['question'].update(metadata or {})
    registry = questions()
    identity = {'protocol': PROTOCOL, 'packet_sha256': digest(packet), 'model': baseline.decisions.MODEL,
                'questions_sha256': digest(registry), 'byte_limits': [baseline.FIRST_BYTES, baseline.SECOND_BYTES],
                'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'baseline_sha256': hashlib.sha256(Path(baseline.__file__).read_bytes()).hexdigest()}
    if (folder/'identity.json').exists() and load(folder/'identity.json') != identity:
        raise ValueError('Frozen coverage chain changed')
    save(folder/'identity.json', identity)
    save(folder/'packet.json', packet)
    first, audit = select(packet)
    save(folder/'first-state.json', first)
    save(folder/'first-input-audit.json', audit)
    if dry_run:
        return {'status': 'prepared', **audit}
    response = baseline.call(first, folder/'first', registry)
    reasons = baseline.route(response)
    # Symmetric audit: a low event head with strongly supported conditions is
    # also a disagreement. Conditions remain opinions, never multiplied facts.
    if response['answers']['event_yes']['noul'] <= .35:
        opposing = [k for k in baseline.CHECKS if response['answers'][k]['probabilities']['supported'] >= .65]
        if opposing:
            reasons = list(dict.fromkeys(reasons + opposing + ['decision_condition_conflict']))
    second, second_audit = select(packet, first, reasons, baseline.SECOND_BYTES) if reasons else (first, audit)
    existing = {s['evidence_id'] for s in first['evidence']}
    added = [s for s in second['evidence'] if s['evidence_id'] not in existing]
    gate = {'reasons': reasons, 'new_ids': [s['evidence_id'] for s in added],
            'new_chars': sum(len(s['text']) for s in added),
            'diagnostics_are_not_facts': True}
    gate['second_call_required'] = bool(reasons) and gate['new_chars'] >= baseline.ROUTING['minimum_new_chars']
    save(folder/'routing.json', gate)
    final = response
    if gate['second_call_required']:
        save(folder/'second-state.json', second)
        save(folder/'second-input-audit.json', second_audit)
        final = baseline.call(second, folder/'second', registry)
    p = final['answers']['event_yes']['noul']
    unresolved = [k for k in baseline.CHECKS if final['answers'][k]['probabilities']['insufficient'] >= .4]
    result = {'status': 'completed', 'probability_yes': p, 'clipped_probability_yes': min(.98, max(.02, p)),
              'first_probability_yes': response['answers']['event_yes']['noul'],
              'second_call_required': gate['second_call_required'], 'routing': gate,
              'unresolved_conditions': unresolved,
              'extreme_with_unresolved_coverage': bool(unresolved) and (p < .1 or p > .9),
              'http_attempt_cap': 2, 'no_retrieval_calls': True, 'no_forecasts_submitted': True,
              'automatic_use_eligible': False}
    save(folder/'result.json', result)
    return result
