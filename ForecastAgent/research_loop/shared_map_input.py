"""Match original coverage and reserve actual map-note headroom in both arms.

This research input experiment changes common original selection explicitly; it
does not impersonate the frozen release's conditional reread selector. Existing
original spans remain intact. Additional text is an exact uncovered portion of a
whole graph binding, never a synthetic paraphrase or a budget-clipped quotation.
"""
import copy
import hashlib
from ForecastAgent.acquisition.handoff import uncovered
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.analysis.mercury_evidence_chain import request_bytes, SECOND_BYTES
from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import decision, forecast_projection

POLICY = 'equal-originals-map-headroom-v1'


def bind_group(bundle, original, bindings):
    result = copy.deepcopy(original)
    packet = packet_for(bundle)
    library = {s['url']: s for s in packet['sources']}
    sources = {s['source_id']: s for s in result['sources']}
    added = []
    for ref in bindings:
        url = ref['url']
        page = bundle['pages'].get(url)
        if page is None or url not in library:
            raise ValueError('Binding original is unavailable to both arms')
        body = page['content']
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        if ref['body_sha256'] != body_hash:
            raise ValueError('Binding original hash changed')
        if ref.get('coordinate_space', 'saved_body') != 'saved_body' or ref.get('view_sha256') not in {None, body_hash}:
            raise ValueError('Decoded view requires a separately frozen common representation')
        start, end = ref['start'], ref['end']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body):
            raise ValueError('Invalid complete binding coordinates')
        source = next((s for s in result['sources'] if s['url'] == url), None)
        if source is not None and source['body_sha256'] != body_hash:
            raise ValueError('Original source identity drift')
        if source is None:
            candidate = library[url]
            source = {k: copy.deepcopy(candidate[k]) for k in
                ('source_id', 'url', 'body_sha256', 'capture_metadata', 'saved_body_truncated') if k in candidate}
            if source['source_id'] in sources:
                raise ValueError('New source ID collides with an existing original')
            result['sources'].append(source)
            sources[source['source_id']] = source
        covered = [(s['start'], s['end']) for s in result['evidence'] if s['source_id'] == source['source_id']]
        for left, right in uncovered(start, end, covered):
            key = digest({'url': url, 'body_sha256': body_hash, 'start': left, 'end': right})
            span = {'evidence_id': 'M' + key[:24], 'source_id': source['source_id'],
                'url': url, 'body_sha256': body_hash, 'start': left, 'end': right, 'text': body[left:right]}
            if any(s['evidence_id'] == span['evidence_id'] for s in result['evidence']):
                raise ValueError('Original evidence ID collision')
            result['evidence'].append(span)
            added.append(span['evidence_id'])
    return result, added


def align(bundle, mapped_bundle, original, heads, *, maximum=SECOND_BYTES, margin=0,
          projection=forecast_projection.LEGACY):
    # request_bytes matches the provider's exact JSON body. Extra padding is an
    # explicit caller preference, not an unrecorded reduction of its hard cap.
    if type(maximum) is not int or type(margin) is not int or not 0 <= margin < maximum <= SECOND_BYTES:
        raise ValueError('Invalid shared forecast request bound')
    forecast_projection.validate(projection)
    def notes_for(candidate):
        notes, note_audit = decision.scoring_map(mapped_bundle, candidate)
        projected, delivery_audit = forecast_projection.project(notes, policy=projection)
        return projected, {**note_audit, 'forecast_projection': delivery_audit}
    if original.get('research_map'):
        raise ValueError('The shared original parent cannot contain a map')
    common = copy.deepcopy(original)
    audit = decision.audit(mapped_bundle)
    record = {'policy': POLICY, 'maximum_request_bytes': maximum, 'margin_bytes': margin,
        'original_parent_sha256': digest(original), 'added_evidence_ids': [], 'groups': [],
        'source_text_changed': False, 'question_changed': False, 'new_http': 0,
        'selection_differs_from_frozen_release': True}
    if audit.get('usable'):
        invalid = set(audit.get('invalid_node_ids', []))
        nodes = mapped_bundle['research_loop']['current']['nodes']
        for node in nodes:
            if node['id'] in invalid or node['kind'] != 'observation' or not node['bindings']:
                continue
            group = {'node_id': node['id']}
            try:
                candidate, added = bind_group(bundle, common, node['bindings'])
                notes, note_audit = notes_for(candidate)
                enriched = {**copy.deepcopy(candidate), 'research_map': notes}
                raw_bytes, mapped_bytes = request_bytes(candidate, heads), request_bytes(enriched, heads)
                reserve = mapped_bytes - raw_bytes + margin
                if not notes.get('factual_grounding_present') or node['id'] in note_audit.get('coverage_omitted_node_ids', []):
                    group.update(status='not_deliverable', reason='Graph binding or node audit is incomplete')
                elif mapped_bytes + margin > maximum:
                    group.update(status='no_room', raw_bytes=raw_bytes, mapped_bytes=mapped_bytes,
                        required_map_room_bytes=reserve)
                else:
                    common = candidate
                    record['added_evidence_ids'].extend(added)
                    group.update(status='accepted', raw_bytes=raw_bytes, mapped_bytes=mapped_bytes,
                        required_map_room_bytes=reserve)
            except ValueError as exc:
                group.update(status='unsupported_binding', reason=str(exc))
            record['groups'].append(group)
    notes, final_audit = notes_for(common)
    mapped = copy.deepcopy(common)
    independent = bool(notes.get('factual_grounding_present'))
    if independent:
        mapped['research_map'] = notes
        if request_bytes(mapped, heads) + margin > maximum:
            independent = False
            mapped = copy.deepcopy(common)
    if any(mapped[k] != value for k, value in common.items()):
        raise ValueError('Mapped arm changed the common original input')
    record.update(status='paired_ready' if independent else 'original_only', independent_input=independent,
        common_original_sha256=digest(common), mapped_sha256=digest(mapped),
        common_original_bytes=request_bytes(common, heads), mapped_bytes=request_bytes(mapped, heads),
        reserved_map_room_bytes=request_bytes(mapped, heads)-request_bytes(common, heads)+margin,
        map_audit=final_audit)
    if request_bytes(common, heads) > maximum:
        raise ValueError('Existing protected originals exceed shared ceiling')
    return common, mapped, record
