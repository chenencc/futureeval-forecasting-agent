"""Market adapter for whole-span reading pages; immutable originals never shrink."""
import copy
import hashlib
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from .common import digest, now, write

VERSION = 'market-whole-span-context-v1'


def identity():
    return {'version': VERSION, 'maximum_chars': 28000,
            'adapter_sha256_lf': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
            'whole_original_spans_only': True, 'rules_changed': False}


def fit_messages(messages, payload, maximum, native_fit=None):
    """Use the native fit first. Page optional graph navigation only if it fails."""
    from ForecastAgent.research_loop import delivery
    from ForecastAgent.runtime.context import ContextProjectionError
    native_fit = native_fit or delivery.fit_messages
    try:
        return native_fit(messages, payload, maximum)
    except ContextProjectionError:
        pass
    candidate = copy.deepcopy(payload)
    frame = candidate.get('research_binding_frame') or {}
    feedback = frame.get('gap_feedback') or {}
    audit = {'original_navigation_sha256': digest(payload), 'rules_changed': False,
             'source_text_changed': False, 'processing_receipt_created': False}
    notice = {'policy': VERSION,
        'instruction': 'Only this reading page is delivered. Omitted navigation remains saved, '
            'not absent or processed. Use inspect_research_state; material_offset=0 for earlier material rows.'}
    candidate['navigation_page'] = notice
    # Previous graph interpretation is navigation, not the pinned original.
    # Keep IDs/claims; detailed prior annotations remain in the graph ledger.
    nodes = candidate.get('current_nodes', [])
    candidate['current_nodes'] = [{k: n[k] for k in
        ('id', 'kind', 'claim', 'evidence_ids', 'gap_reason', 'event_time', 'time_status') if k in n} for n in nodes]
    audit['full_node_ids'] = [n['id'] for n in nodes]
    update = feedback.get('last_update')
    if update:
        feedback['last_update'] = {k: update[k] for k in
            ('map_revision', 'event_sha256', 'disposition_counts') if k in update}
        audit['previous_update_sha256'] = digest(update)
    # Other materials are paginated navigation. Source identity, scope and
    # processing flags for the current reading remain complete and unchanged.
    material_ids = {s.get('material_id') for s in
        frame.get('inspected_evidence', []) + frame.get('source_context', [])}
    materials = feedback.get('pending_materials', [])
    kept = [m for m in materials if m.get('material_id') in material_ids]
    omitted = [m['material_id'] for m in materials if m not in kept]
    if omitted:
        feedback['pending_materials'] = kept
        notice['omitted_material_ids'] = omitted
    try:
        result, selection = native_fit(messages, candidate, maximum)
    except ContextProjectionError:
        # Whole historical node records can be deferred. Complete rule fields,
        # next action, gap meanings and the last exact original remain protected.
        notice['deferred_node_ids'] = list(audit['full_node_ids'])
        candidate['current_nodes'] = []
        try:
            result, selection = native_fit(messages, candidate, maximum)
        except ContextProjectionError:
            # Defer whole historical requests, never their conditions or quotes.
            # Target definitions and current gap meanings remain protected.
            requests = candidate.get('material_requests', [])
            notice['deferred_material_request_count'] = 0
            while requests:
                requests.pop()
                notice['deferred_material_request_count'] += 1
                try:
                    result, selection = native_fit(messages, candidate, maximum)
                    break
                except ContextProjectionError:
                    if not requests:
                        raise
            else:
                raise
    selection['navigation_page'] = copy.deepcopy(notice | audit)
    return result, selection


@contextmanager
def adapter(folder):
    """Declare the runtime override separately from the unchanged pinned engine."""
    from ForecastAgent.research_loop import delivery
    original = delivery.fit_messages
    profile = identity()
    def bounded(messages, payload, maximum):
        result, selection = fit_messages(messages, payload, maximum, original)
        from ForecastAgent.runtime.context import encode
        receipt = {'at': now(), 'runtime': profile, 'request_chars': len(encode(result)),
            'maximum_chars': maximum, 'immutable_question_sha256': digest(payload.get('immutable_question')),
            'input_payload_sha256': digest(payload),
            'selection': selection, 'provider_delivery_confirmed': False,
            'budget_reset': False, 'saved_originals_changed': False}
        root = Path(folder) / 'context-delivery'
        number = len(list(root.glob('projection-*.json'))) + 1
        write(root / f'projection-{number:04d}.json', receipt)
        return result, selection
    write(Path(folder) / 'context-delivery' / 'runtime.json', profile)
    with patch.object(delivery, 'fit_messages', bounded):
        yield
