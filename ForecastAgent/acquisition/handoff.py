"""Offline, opt-in packing of version-bound original material for first analysis.

Source IDs replace repeated URLs/hashes in spans. The source ledger retains
those fields. Old-visible text is protected; banked text is a navigation hint,
never a verified claim. Document coordinates never become guessed body offsets.
"""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.readers.saved import select as saved_select, version_digest
from ForecastAgent.tavily_research import canonical_url

PROTOCOL = 'banked-material-handoff-v1'
IDENTITY_CHARS = 1800


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ranges(values):
    result = []
    for start, end in sorted(values):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def uncovered(start, end, covered):
    cursor = start
    result = []
    for left, right in ranges(covered):
        if right <= cursor or left >= end:
            continue
        if left > cursor:
            result.append([cursor, left])
        cursor = max(cursor, right)
    if cursor < end:
        result.append([cursor, end])
    return result


def space(span):
    return span['source_id'], span.get('document_index')


def compact(spans, pages, sources):
    """Coalesce only overlapping/adjacent ranges in the SAME saved text view."""
    groups = {}
    for span in spans:
        groups.setdefault(space(span), []).append(span)
    result = []
    for (sid, index), group in groups.items():
        _, text, _ = saved_select(pages, sources[sid]['url'], index)
        for start, end in ranges((s['start'], s['end']) for s in group):
            row = {'evidence_id': f'H{len(result)+1:04}', 'source_id': sid,
                   'start': start, 'end': end, 'text': text[start:end]}
            if index is not None:
                row.update(coordinate_space='saved_document', document_index=index)
            result.append(row)
    return result


def validate_bank(bundle, source_map):
    valid, invalid = [], []
    by_url = {s['url']: s for s in source_map.values()}
    for position, excerpt in enumerate(bundle.get('excerpts', [])):
        ref = {'bank_index': position, 'excerpt_id': excerpt.get('id') if isinstance(excerpt,dict) else None}
        try:
            if not isinstance(excerpt,dict):
                raise ValueError('Invalid banked excerpt record')
            url = canonical_url(excerpt['url'])
            source = by_url.get(url)
            if source is None:
                raise ValueError('Source is absent or excluded from the readable body inventory')
            location = excerpt.get('location', {})
            if not isinstance(location, dict):
                raise ValueError('Invalid original coordinate metadata')
            index = location.get('document_index')
            if location.get('coordinate_space') not in ('saved_content', 'saved_document'):
                raise ValueError('Missing or unsupported original coordinate space')
            if location['coordinate_space'] == 'saved_document' and type(index) is not int:
                raise ValueError('Missing document-local index')
            if location['coordinate_space'] == 'saved_content' and index is not None:
                raise ValueError('Body coordinates cannot carry a document index')
            page, text, metadata = saved_select(bundle['pages'], url, index)
            if not page.get('sha256') or page['sha256'] != excerpt.get('source_sha256'):
                raise ValueError('Raw source version mismatch or missing hash')
            if version_digest(page) != excerpt.get('source_parsed_sha256'):
                raise ValueError('Parsed source version mismatch or missing hash')
            start, end = excerpt['start_char'], excerpt['end_char']
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                raise ValueError('Invalid original coordinates')
            if not isinstance(excerpt.get('text'), str) or text[start:end] != excerpt['text']:
                raise ValueError('Banked text differs from the original slice')
            body = page['content']
            # Map only a complete document at one unambiguous exact occurrence.
            # Extracted tables often differ from the whole-body representation.
            offset = 0 if index is None else body.find(text)
            mapped = index is None or (offset >= 0 and body.find(text, offset+1) < 0)
            valid.append({**ref, 'source_id': source['source_id'], 'url': url,
                          'original_document_index': index, 'original_start': start, 'original_end': end,
                          'document_index': None if mapped else index,
                          'start': start+offset if mapped else start, 'end': end+offset if mapped else end,
                          'mapping': 'exact_unique_body' if mapped else 'preserved_document_coordinates',
                          'source_sha256': page['sha256'], 'source_parsed_sha256': version_digest(page),
                          'metadata': metadata, 'truth_verified': False})
        except (KeyError, ValueError, TypeError) as exc:
            invalid.append({**ref, 'reason': str(exc)})
    return valid, invalid


def atoms(text, start, end, table=False):
    """Use complete saved table lines; ordinary prose stays in bounded slices."""
    if table:
        start = text.rfind('\n', 0, start)+1
        if end < len(text) and text[end-1:end] != '\n':
            next_line = text.find('\n', end)
            end = len(text) if next_line < 0 else next_line+1
        cursor = start
        while cursor < end:
            right = text.find('\n', cursor, end)
            right = end if right < 0 else right+1
            yield cursor, right
            cursor = right
    else:
        while start < end:
            right = min(start+900, end)
            line = text.rfind('\n', start, right)
            if line > start+350:
                right = line+1
            yield start, right
            start = right


def identity_atoms(text):
    """Small whole-line context slices within the fixed saved-body prefix."""
    end = min(IDENTITY_CHARS, len(text))
    if end < len(text) and (line := text.rfind('\n',0,end)) >= 0:
        end = line+1
    cursor = 0
    while cursor < end:
        right = min(cursor+300,end)
        if right < end:
            line = text.find('\n',right,end)
            right = end if line < 0 else line+1
        yield cursor,right
        cursor = right


def target_terms(question):
    """Navigation terms come from the proposition/rule, not agent bookkeeping."""
    text = ' '.join(str(question.get(k,'')) for k in ('question','resolution_criteria'))
    return set(re.findall(r'[a-z0-9]{4,}',text.lower()))


def rank_identity(text, terms):
    spans = list(identity_atoms(text))
    tokens = {p:set(re.findall(r'[a-z0-9]{4,}',text[p[0]:p[1]].lower())) for p in spans}
    frequency = {t:sum(t in words for words in tokens.values()) for t in terms}
    # Repeated menu vocabulary must not outweigh a distinctive original label.
    return sorted(spans,key=lambda p:(-sum(1/frequency[t] for t in terms & tokens[p]),p[0]))


def pack(bundle, *, limit=chain.FIRST_BYTES, decision_questions=None):
    """Return a standalone state and audit sidecar. No credentials or I/O calls."""
    from ForecastAgent.acquisition.pipeline import reject_outcomes
    reject_outcomes(bundle['request'])
    packet = packet_for(bundle)
    registry = decision_questions or chain.questions()
    old, old_selection = chain.select(packet, limit=limit, decision_questions=registry)
    source_map = {s['source_id']: copy.deepcopy(s) for s in packet['sources']}
    state = copy.deepcopy(old)
    state['evidence'] = compact(old['evidence'], bundle['pages'], source_map)
    valid, invalid = validate_bank(bundle, source_map)

    def add(sid, index, start, end):
        """An atom either fits intact or is left explicitly out of the request."""
        coverage = [(s['start'], s['end']) for s in state['evidence'] if space(s) == (sid, index)]
        missing = uncovered(start, end, coverage)
        if not missing:
            return True
        trial = copy.deepcopy(state)
        _, text, metadata = saved_select(bundle['pages'], source_map[sid]['url'], index)
        trial['evidence'] = compact(trial['evidence'] + [
            {'source_id': sid, 'start': a, 'end': b, 'document_index': index}
            for a, b in missing], bundle['pages'], source_map)
        ledger = next((s for s in trial['sources'] if s['source_id'] == sid), None)
        if ledger is None:
            ledger = {k: copy.deepcopy(source_map[sid][k]) for k in
                      ('source_id', 'url', 'body_sha256', 'capture_metadata', 'saved_body_truncated')
                      if k in source_map[sid]}
            trial['sources'].append(ledger)
        if index is not None:
            docs = ledger.setdefault('document_views', [])
            if not any(d['document_index'] == index for d in docs):
                # Raw/parsed capture versions remain in the immutable sidecar.
                # The request binds each view to its own exact text hash; do not
                # repeat the source URL or coordinate-space labels as metadata.
                docs.append({'document_index': index,
                             'document_sha256': hashlib.sha256(text.encode()).hexdigest(),
                             'metadata': {k:v for k,v in metadata.items()
                                          if k not in {'source','coordinate_space','document_index'}}})
        if chain.request_bytes(trial, registry) > limit:
            return False
        state.clear()
        state.update(trial)
        return True

    if chain.request_bytes(state, registry) > limit:
        raise ValueError('Protected old evidence exceeds the request byte bound')
    terms = target_terms(packet['question'])
    queues = {}
    dependencies = {}
    for bank in valid:
        sid, index = bank['source_id'], bank['document_index']
        # Different extracted tables may map into the same whole body, while
        # still requiring their own column-header dependency.
        key = (sid, index, bank['original_document_index'])
        _, text, _ = saved_select(bundle['pages'], bank['url'], index)
        table = 'table' in bank['metadata'].get('format', '')
        if table:
            offset = bank['start']-bank['original_start']
            original_index = bank['original_document_index']
            _, table_text, _ = saved_select(bundle['pages'], bank['url'], original_index)
            header_end = table_text.find('\n')+1 if '\n' in table_text else len(table_text)
            dependencies[key] = (offset, offset+header_end)
        queues.setdefault(key, set()).update(atoms(text, bank['start'], bank['end'], table))

    # Reserve one lexical original identity context per banked source before
    # payload rows. Do not spend every remaining byte on a navigation preamble.
    identity = {}
    for sid in dict.fromkeys(b['source_id'] for b in valid):
        body = bundle['pages'][source_map[sid]['url']]['content']
        identity[sid] = rank_identity(body,terms)
        if identity[sid]:
            add(sid,None,*identity[sid][0])

    ranked = {}
    for key, spans in queues.items():
        _, text, _ = saved_select(bundle['pages'], source_map[key[0]]['url'], key[1])
        ranked[key] = sorted(spans, key=lambda p: (
            -len(terms & set(re.findall(r'[a-z0-9]{4,}', text[p[0]:p[1]].lower()))), p[0]))
    for i in range(max((len(v) for v in ranked.values()), default=0)):
        for key, spans in ranked.items():
            sid, index, _ = key
            if i >= len(spans):
                continue
            dependency = dependencies.get(key)
            if dependency and not add(sid, index, *dependency):
                continue
            add(sid, index, *spans[i])

    for i in range(1,max((len(v) for v in identity.values()),default=0)):
        for sid,spans in identity.items():
            if i < len(spans):
                add(sid,None,*spans[i])

    # Spend remaining room on source-balanced lexical body spans. No bank claims,
    # labels, adequacy declarations or target/rubric anchors enter this state.
    body_groups = {}
    for span in packet['evidence']:
        body_groups.setdefault(span['source_id'], []).append(span)
    body_groups = {sid: sorted(spans, key=lambda s: (
        -sum(t in s['text'].lower() for t in terms), s['start'])) for sid, spans in body_groups.items()}
    for i in range(max((len(v) for v in body_groups.values()), default=0)):
        for sid, spans in body_groups.items():
            if i < len(spans):
                add(sid, None, spans[i]['start'], spans[i]['end'])

    bank_delivery = []
    for bank in valid:
        covered = [(s['start'], s['end']) for s in state['evidence']
                   if space(s) == (bank['source_id'], bank['document_index'])]
        missing = uncovered(bank['start'], bank['end'], covered)
        bank_delivery.append({**bank, 'omitted_ranges': missing,
                              'forwarded_chars': bank['end']-bank['start']-sum(b-a for a,b in missing),
                              'complete': not missing, 'omission_reason': 'request_byte_limit' if missing else None})
    lost = []
    for span in old['evidence']:
        covered = [(s['start'], s['end']) for s in state['evidence'] if space(s) == space(span)]
        if uncovered(span['start'], span['end'], covered):
            lost.append(span['evidence_id'])
    if lost:
        raise ValueError('Protected old-visible evidence was removed')
    state['context_omitted'] = any(uncovered(s['start'], s['end'], [
        (e['start'], e['end']) for e in state['evidence'] if space(e) == space(s)]) for s in packet['evidence'])
    manifest = {'schema': PROTOCOL, 'bundle_sha256': digest(bundle), 'state_sha256': digest(state),
                'question_sha256': digest(state['question']), 'decision_questions_sha256': digest(registry),
                'request_bytes': chain.request_bytes(state, registry), 'request_byte_limit': limit,
                'old_request_bytes': old_selection['request_bytes'], 'protected_old_span_ids': old_selection['selected_ids'],
                'old_visible_text_removed': lost, 'valid_bank_count': len(valid), 'invalid_banks': invalid,
                'bank_delivery': bank_delivery,
                'source_versions': {url: {'source_sha256': page.get('sha256'),
                    'source_parsed_sha256': version_digest(page)} for url, page in bundle['pages'].items()},
                'provider_calls': 0, 'analysis_run': False, 'submitted': False,
                'scope': 'Original-text delivery only. Banking does not certify relevance or truth. Not cutoff-safe.'}
    unique_banks = {}
    for bank in valid:
        unique_banks.setdefault((bank['source_id'], bank['document_index']), []).append((bank['start'], bank['end']))
    total = forwarded = 0
    for key, spans in unique_banks.items():
        coverage = [(s['start'], s['end']) for s in state['evidence'] if space(s) == key]
        for start, end in ranges(spans):
            total += end-start
            forwarded += end-start-sum(b-a for a,b in uncovered(start,end,coverage))
    manifest.update(unique_valid_bank_chars=total, unique_forwarded_bank_chars=forwarded,
                    unique_omitted_bank_chars=total-forwarded)
    return state, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from ForecastAgent.acquisition.pipeline import verify_baseline
    verify_baseline()
    raw = args.bundle.read_bytes()
    state, manifest = pack(json.loads(raw))
    manifest['original_bundle_file_sha256'] = hashlib.sha256(raw).hexdigest()
    manifest['implementation_sha256_lf'] = hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    result = {'state': state, 'manifest': manifest}
    encoded = (json.dumps(result, ensure_ascii=False, indent=2)+'\n').encode()
    if args.output.exists() and args.output.read_bytes() != encoded:
        raise ValueError('Output identity changed; use a separate immutable output path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    print(json.dumps({'output': str(args.output), 'request_bytes': manifest['request_bytes'],
                      'valid_banks': manifest['valid_bank_count'], 'invalid_banks': manifest['invalid_banks']}))


if __name__ == '__main__':
    main()
