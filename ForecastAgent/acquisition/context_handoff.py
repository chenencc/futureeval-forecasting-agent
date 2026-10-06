"""Opt-in original-context delivery with unchanged release decision semantics.

Every old-visible coordinate is protected. New material is admitted in complete
context groups, with source/view hashes and explicit omissions. No adequacy,
truth, event interpretation, prediction, provider call or quota change occurs.
"""

import copy
import hashlib
import re

from ForecastAgent.acquisition import handoff
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.acquisition.pipeline import reject_outcomes
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest
from ForecastAgent.competition.mercury import LIVE_WARNING, packet_for
from ForecastAgent.readers.saved import select as saved_select

PROTOCOL = 'v103-complete-context-handoff-v1'
MAX_CONTEXT_CHARS = 3000


def baseline(bundle, *, limit=chain.FIRST_BYTES):
    """Reproduce the immutable binary production worker's first request."""
    reject_outcomes(bundle['request'])
    if bundle['request'].get('question_type', 'binary') != 'binary':
        raise ValueError('This paired pilot freezes binary decision questions')
    packet = packet_for(bundle)
    initial = chain.initial_state(packet)
    initial['evaluation_warning'] = LIVE_WARNING
    initial['instruction'] += (' Forecast the eventual resolution of this currently open question. '
                               'All listed conditions are diagnostic, not a substitute for the event probability.')
    return chain.select(packet, initial, limit=limit)


def paragraph(text, start, end):
    """Return an intact paragraph or complete lines; never fabricate context."""
    separators = list(re.finditer(r'\n[ \t]*\n', text))
    left = max((m.end() for m in separators if m.end() <= start), default=0)
    right = min((m.start() for m in separators if m.start() >= end), default=len(text))
    if right - left <= MAX_CONTEXT_CHARS:
        return left, right
    left = text.rfind('\n', 0, start) + 1
    if end == len(text) or text[end-1:end] == '\n':
        right = end
    else:
        boundary = text.find('\n', end)
        right = len(text) if boundary < 0 else boundary + 1
    return left, right


def table_header(text):
    """Keep the first table line and contiguous delimiter/multilevel headers."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0, 0
    count = 1
    for line in lines[1:4]:
        stripped = line.strip().strip('|').replace(' ', '')
        if stripped and set(stripped) <= set('-:|+\t'):
            count += 1
        else:
            break
    return 0, sum(len(line) for line in lines[:count])


def orientation(text, terms):
    """Use exact original title/date/unit lines, without asserting applicability."""
    rows = list(handoff.identity_atoms(text))
    def rank(row):
        value = text[row[0]:row[1]]
        words = set(re.findall(r'[a-z0-9]{4,}', value.lower()))
        dated = bool(re.search(r'\b(?:19|20)\d{2}\b', value))
        units = bool(re.search(r'(?:%|\b(?:USD|million|billion|percent|units|tonnes|MW)\b)', value, re.I))
        return (-len(words & terms), -int(dated), -int(units), row[0])
    return sorted(rows, key=rank)[:2]


def novel_chars(first, second):
    groups = {}
    for span in second['evidence']:
        groups.setdefault(handoff.space(span), []).append((span['start'], span['end']))
    total = 0
    for key, spans in groups.items():
        covered = [(s['start'], s['end']) for s in first['evidence'] if handoff.space(s) == key]
        for start, end in handoff.ranges(spans):
            total += sum(b-a for a, b in handoff.uncovered(start, end, covered))
    return total


def pack(bundle, *, limit=chain.FIRST_BYTES):
    packet = packet_for(bundle)
    old, old_audit = baseline(bundle, limit=limit)
    sources = {s['source_id']: copy.deepcopy(s) for s in packet['sources']}
    state = copy.deepcopy(old)
    state['evidence'] = handoff.compact(old['evidence'], bundle['pages'], sources)
    valid, invalid = handoff.validate_bank(bundle, sources)
    terms = handoff.target_terms(packet['question'])
    admissions, omissions, seen = [], [], set()

    def add_group(sid, index, context, role):
        context = handoff.ranges(context)
        signature = (sid, index, tuple(tuple(v) for v in context))
        if signature in seen:
            return
        seen.add(signature)
        page, text, metadata = saved_select(bundle['pages'], sources[sid]['url'], index)
        if any(not 0 <= a < b <= len(text) for a, b in context):
            raise ValueError('Context group outside the immutable source')
        record = {'source_id': sid, 'document_index': index, 'ranges': context,
                  'role': role, 'context_semantics_verified': False}
        if any(b-a > MAX_CONTEXT_CHARS for a, b in context):
            omissions.append({**record, 'reason': 'oversized_complete_context'})
            return
        trial = copy.deepcopy(state)
        trial['evidence'] = handoff.compact(trial['evidence'] + [
            {'source_id': sid, 'document_index': index, 'start': a, 'end': b}
            for a, b in context], bundle['pages'], sources)
        ledger = next((s for s in trial['sources'] if s['source_id'] == sid), None)
        if ledger is None:
            ledger = {k: copy.deepcopy(sources[sid][k]) for k in
                      ('source_id', 'url', 'body_sha256', 'capture_metadata', 'saved_body_truncated') if k in sources[sid]}
            trial['sources'].append(ledger)
        if index is not None:
            views = ledger.setdefault('document_views', [])
            if not any(v['document_index'] == index for v in views):
                views.append({'document_index': index,
                              'document_sha256': hashlib.sha256(text.encode()).hexdigest(),
                              'metadata': {k: v for k, v in metadata.items()
                                           if k not in {'source', 'coordinate_space', 'document_index'}}})
        if chain.request_bytes(trial) > limit:
            omissions.append({**record, 'reason': 'request_byte_limit'})
            return
        admissions.append({**record, 'new_coordinate_chars': novel_chars(state, trial)})
        state.clear()
        state.update(trial)

    if chain.request_bytes(state) > limit:
        raise ValueError('Protected original input exceeds the frozen byte budget')
    groups = {}
    for bank in valid:
        sid, index = bank['source_id'], bank['document_index']
        _, text, _ = saved_select(bundle['pages'], bank['url'], index)
        original_index = bank['original_document_index']
        if 'table' in bank['metadata'].get('format', ''):
            _, original, _ = saved_select(bundle['pages'], bank['url'], original_index)
            offset = bank['start']-bank['original_start']
            a, b = table_header(original)
            context = [(offset+a, offset+b)] + list(handoff.atoms(text, bank['start'], bank['end'], table=True))
        else:
            context = [paragraph(text, bank['start'], bank['end'])]
        groups.setdefault(sid, []).append((index, context, 'bank_with_complete_context'))
    # Expand existing first-visible prose boundaries before adding more subjects.
    for span in old['evidence']:
        sid = span['source_id']
        text = bundle['pages'][sources[sid]['url']]['content']
        groups.setdefault(sid, []).append((None, [paragraph(text, span['start'], span['end'])], 'old_span_context'))
    # Source balancing prevents one long article from using all newly freed room.
    for position in range(max((len(v) for v in groups.values()), default=0)):
        for sid, entries in groups.items():
            if position < len(entries):
                index, context, role = entries[position]
                add_group(sid, index, context, role)
    for sid in sources:
        text = bundle['pages'][sources[sid]['url']]['content']
        for row in orientation(text, terms):
            add_group(sid, None, [row], 'original_identity_date_or_unit_context')
    # Filling the remaining request is still lexical, never a semantic verdict.
    rest = {}
    for span in packet['evidence']:
        rest.setdefault(span['source_id'], []).append(span)
    for sid in rest:
        rest[sid].sort(key=lambda s: (-sum(t in s['text'].lower() for t in terms), s['start']))
    for position in range(max((len(v) for v in rest.values()), default=0)):
        for sid, spans in rest.items():
            if position < len(spans):
                span = spans[position]
                text = bundle['pages'][sources[sid]['url']]['content']
                add_group(sid, None, [paragraph(text, span['start'], span['end'])], 'complete_body_context')
    lost = [s['evidence_id'] for s in old['evidence'] if handoff.uncovered(
        s['start'], s['end'], [(e['start'], e['end']) for e in state['evidence'] if handoff.space(e) == handoff.space(s)])]
    errors = audit_spans(bundle, state)
    if lost or errors or state['question'] != old['question']:
        raise ValueError('Original context integrity or preservation failure')
    state['context_omitted'] = any(handoff.uncovered(s['start'], s['end'], [
        (e['start'], e['end']) for e in state['evidence'] if handoff.space(e) == handoff.space(s)]) for s in packet['evidence'])
    report = {'schema': PROTOCOL, 'bundle_sha256': digest(bundle), 'state_sha256': digest(state),
              'request_bytes': chain.request_bytes(state), 'request_byte_limit': limit,
              'baseline_request_bytes': old_audit['request_bytes'], 'old_visible_text_removed': lost,
              'new_coordinate_chars': novel_chars(old, state), 'admissions': admissions, 'omissions': omissions,
              'invalid_banks': invalid, 'source_integrity_errors': errors,
              'model_calls': 0, 'search_calls': 0, 'fetch_calls': 0, 'submitted': False}
    return state, report


def extend(bundle, first, reasons):
    """Use the frozen release selector while deduplicating renamed coordinates."""
    packet = packet_for(bundle)
    frontier = copy.deepcopy(packet)
    frontier['evidence'] = [s for s in packet['evidence'] if handoff.uncovered(
        s['start'], s['end'], [(e['start'], e['end']) for e in first['evidence'] if handoff.space(e) == handoff.space(s)])]
    second, audit = chain.select(frontier, first, reasons, chain.SECOND_BYTES)
    sources = {s['source_id']: s for s in packet['sources']}
    second['evidence'] = handoff.compact(second['evidence'], bundle['pages'], sources)
    lost = [s['evidence_id'] for s in first['evidence'] if handoff.uncovered(
        s['start'], s['end'], [(e['start'], e['end']) for e in second['evidence'] if handoff.space(e) == handoff.space(s)])]
    if lost or audit_spans(bundle, second) or chain.request_bytes(second) > chain.SECOND_BYTES:
        raise ValueError('Second request removed or corrupted original material')
    return second, {'release_selector': audit, 'new_coordinate_chars': novel_chars(first, second),
                    'request_bytes': chain.request_bytes(second), 'old_visible_text_removed': lost}
