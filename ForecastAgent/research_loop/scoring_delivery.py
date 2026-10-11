"""Freeze shared original coverage before scoring; graph hints are not facts."""
import copy
import hashlib

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import reference_map, state

FIELD = 'research_scoring_delivery_policy'
POLICY = 'complete_bound_originals_first_v1'
# These are acquisition bookkeeping, not platform question/scale/rule fields.
# Keep every other field, including unknown platform extensions, unchanged.
LOCAL_METADATA_FIELDS = ('capability_code_identity', 'predictive_information_contract')


def enabled(bundle):
    value = bundle.get('request', {}).get(FIELD, 'disabled')
    if value not in {'disabled', POLICY}:
        raise ValueError('Unknown scoring delivery policy')
    if value == POLICY and not reference_map.enabled(bundle):
        raise ValueError('Bound-original scoring delivery requires reference maps')
    return value == POLICY


def prepare(bundle, admitted):
    """Retain a captured duplicate URL only with identical original bytes.

    No binding is rebound to a representative URL or normalized text. The common
    packet contains the actual captured source URL selected by the map. Both
    scoring arms receive this same selection before either forecast is produced.
    """
    before = digest(bundle), digest(admitted)
    view = copy.deepcopy(admitted)
    removed = []
    for field in LOCAL_METADATA_FIELDS:
        if field in view['request']:
            value = view['request'].pop(field)
            removed.append({'field':field, 'sha256':digest(value),
                            'reason':'Preserved local acquisition metadata; not source evidence.'})
    report = state.audit(bundle)
    invalid = set(report.get('invalid_node_ids', []))
    rows = {r['url']: r for r in admitted.get('predictive_admission', {}).get('sources', [])}
    recovered, rejected, groups = [], [], []
    for node in (bundle.get('research_loop', {}).get('current') or {}).get('nodes', []):
        if node['kind'] != 'observation' or node['id'] in invalid:
            continue
        refs = node.get('bindings', [])
        proposed = {}
        error = None
        for ref in refs:
            page = bundle.get('pages', {}).get(ref['url'])
            text = page.get('content', '') if page else ''
            body_hash = hashlib.sha256(text.encode()).hexdigest()
            if (not page or body_hash != ref['body_sha256'] or
                    type(ref['start']) is not int or type(ref['end']) is not int or
                    not 0 <= ref['start'] < ref['end'] <= len(text) or
                    ref.get('view_sha256', body_hash) != body_hash):
                error = 'invalid_or_nonraw_original_reference'; break
            saved = view.get('pages', {}).get(ref['url'])
            if saved:
                if hashlib.sha256(saved.get('content', '').encode()).hexdigest() != body_hash:
                    error = 'admitted_body_identity_differs'; break
                continue
            row = rows.get(ref['url'], {})
            representative = view.get('pages', {}).get(row.get('representative_url'), {})
            if (row.get('action') != 'duplicate_body' or not representative or
                    hashlib.sha256(representative.get('content', '').encode()).hexdigest() != body_hash):
                error = 'source_not_admitted_or_duplicate_bytes_differ'; break
            proposed[ref['url']] = copy.deepcopy(page)
        if error or not refs:
            rejected.append({'node_id':node['id'], 'reason':error or 'no_original_bindings'})
            continue
        view['pages'].update(proposed)
        recovered.extend({'url':u, 'body_sha256':hashlib.sha256(p['content'].encode()).hexdigest(),
                          'reason':'Retained captured URL; complete byte-identical original, no rebinding.'}
                         for u,p in proposed.items())
        groups.append({'node_id':node['id'], 'references':copy.deepcopy(refs)})
    if before != (digest(bundle), digest(admitted)):
        raise ValueError('Frozen source packet changed during scoring preparation')
    return view, groups, {'policy':POLICY, 'duplicate_source_urls_retained':recovered,
        'rejected_node_groups':rejected, 'binding_urls_rewritten':False,
        'local_metadata_omitted_from_scoring_question':removed,
        'remaining_question_fields_preserved':True,
        'same_selection_before_both_forecasts':True, 'source_relevance_verified':False,
        'model_http':0, 'searches':0, 'fetches':0}
