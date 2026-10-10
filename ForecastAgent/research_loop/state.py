"""Versioned interpretations with exact, independently checked source bindings."""
import copy
import hashlib
import re
from datetime import datetime, timezone

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.analysis.referenced import units
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.runtime.collection_v2 import eligible
from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.research_loop import POLICY, VERSION
from ForecastAgent.research_loop.schema import PROPOSAL, NODE

MAX_UPDATES = 3
RULE_FIELDS = ('question', 'resolution_criteria', 'fine_print', 'background')


def now():
    return datetime.now(timezone.utc).isoformat()


def enabled(bundle):
    return bundle.get('request', {}).get('research_state_policy') == POLICY


def catalog(bundle, cutoff=None):
    """No search snippets, blocked bodies or reconstructed cells enter the map."""
    spans, sources, excluded = {}, {}, []
    windows = bundle.get('research_map_visible_references')
    if windows is not None:
        if not isinstance(windows, list) or not windows:
            raise ValueError('Visible map references must be a nonempty coordinate list')
        seen = set()
        for window in windows:
            if (not isinstance(window, dict) or set(window) != {'url', 'body_sha256', 'start', 'end'} or
                    type(window['start']) is not int or type(window['end']) is not int or
                    not 0 <= window['start'] < window['end']):
                raise ValueError('Invalid visible map reference coordinates')
            key = (window['url'], window['body_sha256'], window['start'], window['end'])
            if key in seen:
                raise ValueError('Duplicate visible map reference coordinates')
            seen.add(key)
    for url, page in sorted(bundle.get('pages', {}).items()):
        body = page.get('content', '')
        if not isinstance(body, str):
            raise ValueError('Saved source content must be text')
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        if page.get('content_sha256') and page['content_sha256'] != body_hash:
            raise ValueError('Saved source body checksum mismatch')
        diagnostics = body_diagnostics(body)
        if (page.get('body_diagnostics', {}).get('usable_text') is False or
                not diagnostics['usable_text'] or (cutoff and not eligible(page, cutoff))):
            excluded.append({'url': url, 'reason': 'unreadable_or_temporally_blocked'})
            continue
        sources[url] = {'url': url, 'body_sha256': body_hash,
            'capture_time': page.get('retrieved_at_utc'),
            'publication_time': page.get('published_at') or page.get('published_date'),
            'original_response_sha256': page.get('sha256'),
            'truncated': bool(page.get('content_truncated'))}
        if windows is None:
            from ForecastAgent.research_loop.fusion import POLICY_FIELD, POLICY_NAME
            if bundle['request'].get(POLICY_FIELD) == POLICY_NAME:
                from ForecastAgent.research_loop.forecast_brief_refs import blocks
                from ForecastAgent.research_loop import reading_views
                view=reading_views.reading(body,url) if reading_views.enabled(bundle) else {'text':body}
                ranges=reading_views.partitions(view) if reading_views.enabled(bundle) else blocks(body)
                representation=reading_views.metadata(view) if reading_views.enabled(bundle) else {}
                if representation:
                    sources[url]['reading_view']=representation
                    sources[url]['omitted_inline_image_characters']=sum(b-a for a,b in view.get('omitted_inline_image_ranges',[]))
                original_spans = [{'source_id':url, 'url':url, 'body_sha256':body_hash,
                    'start':a, 'end':b, 'text':view['text'][a:b], **representation,
                    **({'json_provenance':[p for p in view['provenance'] if p['start']<b and p['end']>a]} if 'provenance' in view else {})}
                    for a,b in ranges if view['text'][a:b].strip()]
            else:
                original_spans = units(body, 0, {'source_id': url, 'url': url,
                                               'body_sha256': body_hash}, 1)
        else:
            selected = sorted((w for w in windows if w['url'] == url), key=lambda w: w['start'])
            previous_end = 0
            for w in selected:
                if w['body_sha256'] != body_hash or w['end'] > len(body) or w['start'] < previous_end:
                    raise ValueError('Visible map reference checksum, bounds or overlap mismatch')
                previous_end = w['end']
            original_spans = [{**w, 'source_id': url, 'text': body[w['start']:w['end']]}
                              for w in selected]
        for span in original_spans:
            handle = 'R' + digest({k: span[k] for k in ('url', 'body_sha256', 'start', 'end','view_sha256') if k in span})[:24]
            spans[handle] = {**span, 'evidence_id': handle}
    if windows is not None and any(w['url'] not in sources for w in windows):
        raise ValueError('Visible map reference points to an unavailable source')
    material_identity = sources if windows is None else {'sources': sources, 'visible_references': windows}
    return {'material_sha256': digest(material_identity), 'sources': sources, 'spans': spans,
            'excluded_sources': excluded, 'visible_windows': copy.deepcopy(windows)}


def initialize(bundle):
    policy = bundle.get('request', {}).get('research_state_policy', 'disabled')
    if policy not in {'disabled', POLICY}:
        raise ValueError('Unknown research state policy')
    if policy == 'disabled':
        return None
    if bundle.get('pipeline', 'collection') != 'collection':
        raise ValueError('Incremental research requires collection mode')
    rules = {key: bundle['request'].get(key, '') for key in RULE_FIELDS}
    ledger = bundle.get('research_loop')
    if ledger is not None:
        if ledger.get('schema') != VERSION or ledger.get('rules_sha256') != digest(rules):
            raise ValueError('Research state rules or schema changed')
        from ForecastAgent.research_loop.dispatch import limits
        if ledger.get('update_cap') != limits(bundle['request'])['map_updates']:
            raise ValueError('Frozen map allowance changed')
        verify_journal(ledger)
        return ledger
    from ForecastAgent.research_loop.dispatch import limits
    ledger = {'schema': VERSION, 'policy': POLICY, 'rules': rules,
        'rules_sha256': digest(rules), 'revision': 0,
        'update_cap': limits(bundle['request'])['map_updates'],
        'events': [], 'current': None, 'truth_verified': False,
        'source_independence_verified': False, 'probability_head': False}
    bundle['research_loop'] = ledger
    return ledger


def verify_journal(ledger):
    previous = None
    for revision, event in enumerate(ledger['events'], 1):
        data = {k: v for k, v in event.items() if k != 'event_sha256'}
        if (event.get('revision') != revision or event.get('previous_event_sha256') != previous or
                digest(data) != event.get('event_sha256')):
            raise ValueError('Research event chain checksum mismatch')
        previous = event['event_sha256']
    if (type(ledger.get('update_cap')) is not int or not 3 <= ledger['update_cap'] <= 12 or
            ledger.get('revision') != len(ledger['events']) or
            ledger['revision'] > ledger['update_cap'] or
            ledger.get('current') != (ledger['events'][-1]['state'] if ledger['events'] else None)):
        raise ValueError('Research revision, cap or current state mismatch')


def bind_node(original, material, allowed=None, *, validate_stage=False):
    """Validate one complete node; a bad reference never leaves its claim behind."""
    check_schema(original, NODE)
    node = copy.deepcopy(original)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', node['id']):
        raise ValueError('Node identifier must be safe')
    refs = node['evidence_ids']
    if not node['claim'].strip() or len(set(refs)) != len(refs):
        raise ValueError('Claims must be nonempty and references unique')
    if node['kind'] == 'observation' and not refs:
        raise ValueError('An observation requires an exact saved-body reference')
    if node['kind'] == 'unknown' and node['gap_reason'] == 'none':
        raise ValueError('Unknown nodes must retain a gap reason')
    if node['kind'] == 'observation' and node['gap_reason'] != 'none':
        raise ValueError('An observation is separate from an unresolved gap')
    if node['time_status'] == 'source_stated' and (not refs or not node['event_time'].strip()):
        raise ValueError('Source-stated event time requires text and source references')
    if node['kind'] == 'unknown' and node['time_status'] == 'source_stated':
        raise ValueError('Unknown event timing is not source-established timing')
    bindings = []
    for ref in refs:
        if allowed is not None and ref not in allowed:
            raise ValueError('Reference outside common original coverage: ' + ref)
        if ref not in material['spans']:
            raise ValueError('Unknown or stale research evidence handle: ' + ref)
        span = material['spans'][ref]
        bindings.append({**copy.deepcopy(span), 'source_metadata': material['sources'][span['url']],
                         'binding_verified': True, 'meaning_verified': False})
    if node['time_status'] == 'source_stated' and not any(node['event_time'] in ref['text'] for ref in bindings):
        raise ValueError('Source-stated event_time must copy literal source text; its event role remains unverified')
    if 'claim_origin' in node:
        expected = 'source_quote' if node['kind'] == 'observation' else 'gap' if node['kind'] == 'unknown' else 'hypothesis'
        if node['claim_origin'] != expected:
            raise ValueError('Claim origin must agree with the node kind')
        if expected == 'source_quote' and not any(node['claim'] in ref['text'] for ref in bindings):
            raise ValueError('Observation claim must be a contiguous literal quote in its bound original span')
        # Quotation checks establish provenance, not the source's truth or the
        # model's interpretation of event stage, period or applicability.
        node['literal_quote_verified'] = expected == 'source_quote'
    if validate_stage and node.get('stage_basis'):
        if not any(node['stage_basis'] in ref['text'] for ref in bindings):
            raise ValueError('Stage basis must copy a literal quote in bound original text')
        node['stage_basis_binding_verified'] = True
    elif validate_stage and 'stage_basis' in node:
        node['stage_basis_binding_verified'] = False
    node.update(bindings=bindings, interpretation_verified=False)
    return node


def update(bundle, proposal, cutoff=None):
    """Validate everything before mutating the journal or any raw evidence."""
    if not enabled(bundle):
        raise ValueError('Research state policy is disabled')
    check_schema(proposal, PROPOSAL)
    ledger = initialize(bundle)
    proposal_hash = digest(proposal)
    if ledger['events'] and ledger['events'][-1]['proposal_sha256'] == proposal_hash:
        return {'revision': ledger['revision'], 'cached': True, 'no_progress': True}
    if proposal['expected_revision'] != ledger['revision']:
        raise ValueError('Stale research revision; inspect the current state')
    if ledger['revision'] >= ledger['update_cap']:
        raise ValueError('Research lifetime update cap exhausted')
    material = catalog(bundle, cutoff)
    if proposal['material_sha256'] != material['material_sha256']:
        raise ValueError('Material changed since inspection; inspect new source handles')
    if (ledger['current'] and ledger['current']['material_sha256'] == material['material_sha256'] and
            proposal['revision_kind'] != 'interpretation_correction'):
        raise ValueError('No new material; avoid unchanged-map bookkeeping')
    for name in ('supporting_path', 'alternative_path', 'revision_reason'):
        if not proposal[name].strip():
            raise ValueError(name + ' must be explicit')
    ids = [node['id'] for node in proposal['nodes']]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', i) for i in ids):
        raise ValueError('Node identifiers must be unique and safe')
    known = set(ids)
    retired = proposal['retired_node_ids']
    old = {node['id']: node for node in (ledger['current'] or {}).get('nodes', [])}
    if len(set(retired)) != len(retired) or set(retired) != set(old) - known:
        raise ValueError('Every removed node must be explicitly retired')
    from ForecastAgent.research_loop import grounding
    nodes = [bind_node(original, material, validate_stage=grounding.enabled(bundle)) for original in proposal['nodes']]
    for relation in proposal['relations']:
        if relation['from_id'] not in known or relation['to_id'] not in known or not relation['rationale'].strip():
            raise ValueError('Relations require current nodes and a rationale')
    for need in proposal['material_requests']:
        if not need['target'].strip() or not need['reason'].strip() or not set(need['node_ids']) <= known:
            raise ValueError('Material requests require a concrete target and current node IDs')
    state = {key: copy.deepcopy(proposal[key]) for key in
        ('supporting_path', 'alternative_path', 'material_requests', 'retired_node_ids', 'revision_reason')}
    state.update(nodes=nodes, relations=[{**r, 'verified': False} for r in proposal['relations']],
        material_sha256=material['material_sha256'], excluded_sources=material['excluded_sources'],
        revision=ledger['revision'] + 1, rules_sha256=ledger['rules_sha256'])
    if ledger['current']:
        changed = any(state[k] != ledger['current'][k] for k in
            ('nodes', 'relations', 'supporting_path', 'alternative_path', 'material_requests', 'retired_node_ids'))
        if not changed:
            raise ValueError('Unchanged interpretations do not justify a map revision')
    event = {'revision': state['revision'], 'at_utc': now(),
        'revision_kind': proposal['revision_kind'],
        'previous_event_sha256': ledger['events'][-1]['event_sha256'] if ledger['events'] else None,
        'proposal_sha256': proposal_hash, 'state': state,
        'resources_snapshot': {key: len(bundle.get(key, [])) for key in
            ('searches', 'exa_searches', 'fetch_attempts', 'model_attempts')}}
    event['event_sha256'] = digest(event)
    ledger['events'].append(event)
    ledger.update(revision=state['revision'], current=state)
    return {'revision': state['revision'], 'event_sha256': event['event_sha256'],
            'binding_count': sum(len(n['bindings']) for n in nodes), 'no_progress': True,
            'truth_verified': False}


def audit(bundle, cutoff=None):
    ledger = initialize(bundle) if enabled(bundle) else None
    if not ledger:
        return {'status': 'disabled', 'usable': False, 'binding_errors': []}
    material = catalog(bundle, cutoff)
    current = ledger['current']
    errors = []
    for node in (current or {}).get('nodes', []):
        for ref in node['bindings']:
            present = material['spans'].get(ref['evidence_id'])
            if not present or any(ref[key] != present[key] for key in
                ('url', 'body_sha256', 'start', 'end', 'text')):
                errors.append({'node_id': node['id'], 'evidence_id': ref['evidence_id']})
    invalid = {e['node_id'] for e in errors}
    grounded = any(n['kind'] == 'observation' and n['bindings'] and n['id'] not in invalid
                 for n in (current or {}).get('nodes', []))
    from ForecastAgent.research_loop import delta
    review_pending=bool(delta.enabled(bundle) and current and
        bundle.get('research_acquisition',{}).get('pending_map_update'))
    material_changed = bool(current and current['material_sha256'] != material['material_sha256'])
    processing = None
    from ForecastAgent.research_loop import gap_feedback
    if gap_feedback.enabled(bundle):
        from types import SimpleNamespace
        processing = gap_feedback.coverage(SimpleNamespace(bundle=bundle, cutoff=cutoff))
        review_pending = bool(processing['pending_material_count'])
        # Current receipts acknowledge all current saved source versions without
        # overwriting the historical graph state or manufacturing a revision.
        stale = review_pending
    else:
        stale = material_changed or review_pending
    acceptance = ledger['events'][-1].get('acceptance', {}) if ledger['events'] else {}
    gap_only = bool(current and current['nodes'] and not errors and not stale and
        acceptance.get('status') == 'gap_only' and
        all(n['kind'] == 'unknown' and n['gap_reason'] != 'none' for n in current['nodes']))
    usable = grounded or gap_only
    return {'status': 'unbuilt' if not current else 'partial_invalid_bindings' if errors and usable else 'invalid_bindings' if errors else 'partial_new_material' if stale else 'gap_only_unverified' if gap_only else 'bound_unverified',
            'usable': usable, 'binding_errors': errors, 'invalid_node_ids': sorted(invalid),
            'factual_grounding_present': grounded, 'gap_only': gap_only,
            'pending_saved_material_review':review_pending,
            'unreviewed_material_change': stale,
            'graph_material_changed': material_changed,
            'material_processing': processing,
            'revision': ledger['revision'], 'remaining_updates': ledger['update_cap'] - ledger['revision'],
            'material_sha256': material['material_sha256'], 'truth_verified': False}


def view(bundle, cutoff=None):
    report = audit(bundle, cutoff)
    current = (bundle.get('research_loop') or {}).get('current') or {}
    from ForecastAgent.research_loop import target_logic
    coverage = {'target_coverage': target_logic.brief(bundle)} if target_logic.enabled(bundle) else {}
    return {**report, **coverage, 'nodes': [{k: n[k] for k in ('id', 'kind', 'claim', 'evidence_ids', 'gap_reason')}
        for n in current.get('nodes', [])], 'material_requests': current.get('material_requests', []),
        'instruction': 'Fallible research notes; acquire counterevidence and gaps within the frozen budget. Use inspect_research_state for exact spans and revisions.'}
