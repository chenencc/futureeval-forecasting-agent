"""Gap-directed acquisition and bounded source-review receipts, without scoring."""
import copy
from collections import Counter

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import state, delta
from ForecastAgent.research_loop.schema import NEED, obj, text, array
from ForecastAgent.runtime.contracts import check_schema, ContractError

FIELD = 'research_gap_policy'
POLICY = 'critical_gap_feedback_v1'
LINK = 'research_gap_ids'
MAX_EVENTS = state.MAX_UPDATES * 4


def event_cap(task):
    return task.bundle['research_loop']['update_cap'] * 4
REVIEW = obj({'material_id': text(32),
    'disposition': {'type': 'string', 'enum':
        ['incorporated', 'conflict', 'duplicate', 'irrelevant', 'deferred']},
    # Match the native retained reading-handle limit; receipts can span a table.
    'evidence_ids': array(text(32), 16), 'node_ids': array(text(40), 4),
    'gap_ids': {**array(text(32), 4), 'description':
        'Copy only listed gap_id values (G...). Never use node IDs here. Use [] when no listed gap applies; new material_requests get program IDs after acceptance.'},
    'related_material_ids': array(text(32), 3),
    'effect': {'type': 'string', 'enum': ['supports', 'opposes', 'narrows', 'no_change', 'unknown']},
    'reason': {**text(800), 'description':'Prefer a concise reason under 240 characters. Full audit storage permits at most 800; context projects counts rather than the full receipt prose.'}})
GUIDE = '''
Gap feedback: material_requests state decision_impact (what could change),
importance and availability. Prioritize consequential, obtainable gaps; future
events remain uncertain. research_gap_ids links the next native action to a listed
gap; if no gap exists, use research_node_ids for the frozen plan. Ranking is advice,
not a new quota. Read saved material before acquiring another copy.
Each newly read source gets material_reviews: incorporated, conflict, duplicate,
irrelevant or deferred, with a concrete reason and supplied material/reference IDs.
Incorporated requires a retained observation bound to THAT source. Conflict keeps
both observations. Duplicate names the related source; irrelevant needs a read
reference. Deferred preserves unfinished work, never NO. effect and gap relevance
are fallible judgments, not probabilities. Do not force irrelevant text into nodes.
Use revision_reason to explain what changed or why the graph did not change.
The program records the actual graph delta separately. Only reviewed source
versions are acknowledged; unreviewed sources remain explicit. No nested calls.
Copy short literal quotes from one supplied span. Receipt references may include
up to 16 inspected handles of that source; cite only those actually needed.
Receipt node_ids must name retained, source-bound observations. A proposal that
was rejected did not change the graph. Future target-date releases are future_event,
even when an official landing page is available today.
Material metadata lists inspected_reference_ids and bound_observation_node_ids
for THAT URL/body/scope. A retained node from another source is not an incorporation
of the newly read source. Add a short source-bound observation or explicitly defer.
Past observations can inform a future forecast as background. Being earlier than
the target date does not alone make a source irrelevant. A future result is not
obtainable now: seek an available baseline or leading indicator, not its realization.
Whitespace and ordinary HTTP Markdown link display may be mapped back to exact
original coordinates. Facts, numbers, dates, polarity and units cannot be repaired.
'''


def enabled(bundle):
    return bundle.get('request', {}).get(FIELD) == POLICY


def initialize(task):
    policy = task.bundle['request'].get(FIELD, 'disabled')
    if policy not in {'disabled', POLICY}:
        raise ValueError('Unknown research gap policy')
    if policy == 'disabled':
        return None
    from ForecastAgent.research_loop import fusion
    if not fusion.enabled(task) or not delta.enabled(task.bundle):
        raise ValueError('Gap feedback requires map-guided acquisition and explicit delta updates')
    rules = digest({k: task.bundle['request'].get(k, '') for k in state.RULE_FIELDS})
    ledger = task.bundle.setdefault('research_gap_feedback',
        {'schema': POLICY, 'rules_sha256': rules, 'events': [], 'meaning_verified': False})
    if ledger['schema'] != POLICY or ledger['rules_sha256'] != rules or len(ledger['events']) > event_cap(task):
        raise ValueError('Gap feedback identity or lifetime cap changed')
    previous = None
    for event in ledger['events']:
        if event.get('previous_event_sha256') != previous or digest(
                {k:v for k,v in event.items() if k != 'event_sha256'}) != event.get('event_sha256'):
            raise ValueError('Gap feedback journal checksum mismatch')
        previous = event['event_sha256']
    return ledger


def materials(task, material=None):
    """A receipt applies to this body AND visible reading scope, never an entire site."""
    material = material or state.catalog(task.bundle, task.cutoff)
    result = {}
    for url, source in material['sources'].items():
        refs = [s for s in material['spans'].values() if s['url'] == url]
        scope = digest(sorted(r['evidence_id'] for r in refs))
        ident = 'M' + digest({'url':url, 'body':source['body_sha256'], 'scope':scope})[:24]
        result[ident] = {'material_id':ident, 'url':url, 'body_sha256':source['body_sha256'],
            'scope_sha256':scope, 'capture_time':source['capture_time'],
            'reference_ids':[r['evidence_id'] for r in refs],
            'scope':'Current saved visible text only; interpretation is unverified.'}
        actions=[e for e in task.bundle.get('research_acquisition',{}).get('events',[])
                 if any(b['url']==url and b['body_sha256']==source['body_sha256'] for b in e['new_bodies'])]
        result[ident]['acquisition_action_ids']=[e['index'] for e in actions]
        result[ident]['action_intent_gap_ids']=sorted({i for e in actions for i in e.get(LINK,[])})
        result[ident]['origin']='native_action' if actions else 'saved_input_or_external_stage'
        from ForecastAgent.research_loop import fusion
        delivered={r['evidence_id'] for r in fusion.inspected_references(task,material)}
        delivered.update(r['evidence_id'] for r in task.bundle.get('research_acquisition',{}).get('inspected_context_references',[])
                         if r['evidence_id'] in material['spans'])
        result[ident]['inspected_reference_ids']=[r['evidence_id'] for r in refs if r['evidence_id'] in delivered][:16]
        result[ident]['bound_observation_node_ids']=[n['id'] for n in
            (task.bundle.get('research_loop',{}).get('current') or {}).get('nodes',[])
            if n['kind']=='observation' and any(r['evidence_id'] in result[ident]['reference_ids']
                and r['body_sha256']==source['body_sha256'] and r['url']==url for r in n['bindings'])]
    return result


def gaps(task, current=None):
    current = current if current is not None else task.bundle['research_loop'].get('current') or {}
    result = []
    for need in current.get('material_requests', []):
        ident = 'G' + digest({k:need[k] for k in ('target','role','node_ids')})[:24]
        result.append({'gap_id':ident, **copy.deepcopy(need), 'meaning_verified':False})
    actions = task.bundle.get('research_acquisition', {}).get('events', [])
    for gap in result:
        linked = [e for e in actions if gap['gap_id'] in e.get(LINK, [])]
        gap['attempts'] = len(linked)
        gap['attempts_without_readable_body'] = sum(not any(b['usable_text'] for b in e['new_bodies']) for e in linked)
    # These are declared priorities, not estimates of numerical information gain.
    return sorted(result, key=lambda g: (
        {'available':0,'uncertain':1,'future_event':2}.get(g.get('availability'),1),
        {'high':0,'medium':1,'low':2}.get(g.get('importance'),1), g['attempts'], g['gap_id']))


def identifiers(task, inventory=None):
    """Expose distinct namespaces; no generated alias can repair a wrong link."""
    inventory = inventory if inventory is not None else materials(task)
    current = task.bundle['research_loop'].get('current') or {}
    listed = gaps(task)
    return {'current_node_ids': [n['id'] for n in current.get('nodes', [])],
        'current_gap_ids': [g['gap_id'] for g in listed], 'current_gaps': listed,
        'materials': [{'material_id': m['material_id'], 'url': m['url'],
            'inspected_reference_ids': m['inspected_reference_ids'],
            'bound_observation_node_ids': m['bound_observation_node_ids']}
            for m in inventory.values()],
        'instruction': 'node_ids name retained map nodes; gap_ids copy current_gap_ids '
            'only, or []. material_id names one exact saved source version; evidence_ids '
            'must be inspected references of THAT material. New proposed node IDs are '
            'usable only if their observations survive validation. A rejected node cannot '
            'support incorporation. New material requests receive G IDs after acceptance.'}


def coverage(task, inventory=None):
    """Derive current excerpt processing from intact receipts and retained bindings.

    The graph's original material hash is historical. A later irrelevant or
    duplicate source does not require a fictitious graph edit to acknowledge it.
    Changed bodies/scopes, deferred work and lost incorporation stay pending.
    """
    inventory = inventory if inventory is not None else materials(task)
    ledger = initialize(task)
    latest = {r['material_id']: (r, e['event_sha256']) for e in ledger['events']
              for r in e['accepted_reviews']}
    nodes = {n['id']: n for n in (task.bundle['research_loop'].get('current') or {}).get('nodes', [])}
    material = state.catalog(task.bundle, task.cutoff)
    reviewed, remaining = [], []
    for ident, source in inventory.items():
        item = latest.get(ident)
        reason = 'no_current_receipt'
        if item:
            receipt, event_hash = item
            refs = set(receipt['evidence_ids'])
            disposition = receipt['disposition']
            stored = receipt.get('source', {})
            valid_source = all(stored.get(k) == source[k] for k in
                               ('material_id', 'url', 'body_sha256', 'scope_sha256'))
            bound = [nodes[i] for i in receipt['node_ids'] if i in nodes
                     and nodes[i]['kind'] == 'observation' and any(
                         r['evidence_id'] in refs and r['evidence_id'] in material['spans']
                         and all(r.get(k) == material['spans'][r['evidence_id']].get(k)
                                 for k in ('url', 'body_sha256', 'start', 'end', 'text'))
                         for r in nodes[i].get('bindings', []))]
            related = set(receipt['related_material_ids'])
            reason = ('deferred' if disposition == 'deferred' else
                      'stale_receipt_source' if not valid_source else
                      'stale_receipt_references' if not refs or not refs <= set(source['reference_ids']) else
                      'retired_or_changed_incorporation' if disposition in {'incorporated', 'conflict'} and not bound else
                      'stale_duplicate_parent' if disposition == 'duplicate' and
                          (not related or ident in related or not related <= set(inventory)) else None)
            if reason is None and disposition == 'conflict':
                observations = [nodes[i] for i in receipt['node_ids'] if i in nodes and nodes[i]['kind'] == 'observation']
                if len(observations) < 2 or len({r['body_sha256'] for n in observations for r in n['bindings']}) < 2:
                    reason = 'retired_or_changed_conflict'
            if reason is None:
                reviewed.append({'material_id': ident, 'disposition': disposition,
                                 'receipt_event_sha256': event_hash})
                continue
        remaining.append({'material_id': ident, 'reason': reason})
    current = task.bundle['research_loop'].get('current') or {}
    return {'status': 'processing_with_gaps' if remaining else 'processing_complete',
        'pending_material_count': len(remaining), 'pending': remaining,
        'reviewed': reviewed, 'material_sha256': material['material_sha256'],
        'map_revision': task.bundle['research_loop']['revision'],
        'graph_material_sha256': current.get('material_sha256'),
        'full_document_reading_verified': False, 'truth_verified': False,
        'scope': 'Validated excerpt-disposition receipts only; not target evidence adequacy.'}


def pending(task, inventory=None):
    inventory = inventory if inventory is not None else materials(task)
    ids = {r['material_id'] for r in coverage(task, inventory)['pending']}
    return [m for ident, m in inventory.items() if ident in ids]


def reconcile(task):
    """Repair a scheduling latch from receipts without editing any graph event."""
    result = coverage(task)
    task.bundle['research_acquisition']['pending_map_update'] = bool(result['pending_material_count'])
    return result


def view(task, offset=0):
    inventory = materials(task)
    remaining = pending(task, inventory)
    ledger = initialize(task)
    last = ledger['events'][-1] if ledger['events'] else None
    return {'policy':POLICY, 'gaps':gaps(task),
        'pending_materials':[{k:v for k,v in m.items() if k != 'reference_ids'} for m in remaining[offset:offset+4]],
        'pending_material_count':len(remaining),
        'next_material_offset':offset+4 if offset+4 < len(remaining) else None,
        'last_update':{'map_revision':last['map_revision'], 'actual_graph_delta':last['actual_graph_delta'],
            'model_explanation':last['model_explanation'],
            'program_explanation':last.get('program_explanation'),
            'disposition_counts':dict(Counter(r['disposition'] for r in last['accepted_reviews'])),
            'rejected_reviews':last['rejected_reviews'][:3], 'event_sha256':last['event_sha256']} if last else None,
        'meaning_verified':False,
        'instruction':'Prioritize consequential obtainable gaps; read pending saved sources locally. A receipt does not certify relevance, full-page reading, causal truth or closure of a gap.'}


def schema(base, envelope=False):
    result = copy.deepcopy(base)
    # A review-only merge can preserve an existing graph without resending nodes.
    # The native acceptor still requires a nonempty grounded map after expansion.
    result['properties']['nodes']['minItems'] = 0
    need = result['properties']['material_requests']['items']
    if not envelope:
        need['required'] = list(NEED['properties'])
    result['properties']['material_reviews'] = array({} if envelope else REVIEW, 16)
    result['required'].append('material_reviews')
    return result


def action_links(task, args):
    """Validate identifiers before the native action reserves any provider quota."""
    ids = args.pop(LINK, [])
    known = {g['gap_id']:g for g in gaps(task)}
    if not isinstance(ids, list) or len(ids) > 4 or any(not isinstance(i,str) or i not in known for i in ids):
        raise ContractError('unknown_research_gap', LINK, 'Copy listed gap IDs; do not invent a source or condition.', sorted(known))
    ids = list(dict.fromkeys(ids))
    return {LINK:ids, 'gap_intents':[copy.deepcopy(known[i]) for i in ids]}


def graph_delta(before, after):
    old = {n['id']:delta.node_input(n) for n in (before or {}).get('nodes', [])}
    new = {n['id']:delta.node_input(n) for n in (after or {}).get('nodes', [])}
    old_edges = {digest(r):r for r in (before or {}).get('relations', [])}
    new_edges = {digest(r):r for r in (after or {}).get('relations', [])}
    return {'added_node_ids':sorted(set(new)-set(old)),
        'changed_node_ids':sorted(i for i in set(old)&set(new) if old[i] != new[i]),
        'retired_node_ids':sorted(set(old)-set(new)),
        'added_relation_count':len(set(new_edges)-set(old_edges)),
        'removed_relation_count':len(set(old_edges)-set(new_edges)),
        'material_requests_changed':(before or {}).get('material_requests', []) != (after or {}).get('material_requests', [])}


def explain_applied(change, accepted, rejected, acceptance):
    """Describe accepted edits mechanically, independently of model prose."""
    counts = dict(Counter(r['disposition'] for r in accepted))
    rejected_nodes = sorted({r['node_id'] for r in acceptance.get('rejected', [])
        if r.get('section') == 'nodes' and r.get('node_id')})
    return {'node_changes':{k:change[k] for k in
            ('added_node_ids','changed_node_ids','retired_node_ids')},
        'relation_changes':{k:change[k] for k in ('added_relation_count','removed_relation_count')},
        'material_requests_changed':change['material_requests_changed'],
        'accepted_material_dispositions':counts,
        'rejected_material_review_count':len(rejected),
        'rejected_proposed_node_ids':rejected_nodes,
        'quote_format_binding_count':sum(r['applied'] for r in acceptance.get('quote_format_bindings',[])),
        'instruction':'Only these edits were applied. Rejected proposals and unreviewed material are not graph changes. No causal meaning or probability effect is certified.'}


def review(task, proposals, before, explanation, result):
    """Validate receipts against the accepted graph; no semantic truth certificates."""
    ledger = initialize(task)
    material = state.catalog(task.bundle, task.cutoff)
    inventory = materials(task, material)
    from ForecastAgent.research_loop import fusion
    inspected = {r['evidence_id'] for r in fusion.inspected_references(task, material)}
    inspected.update(r['evidence_id'] for r in task.bundle['research_acquisition'].get('inspected_context_references', [])
                     if r['evidence_id'] in material['spans'])
    current = task.bundle['research_loop'].get('current') or {}
    nodes = {n['id']:n for n in current.get('nodes', [])}
    inspected.update(r['evidence_id'] for n in nodes.values() for r in n.get('bindings', []))
    known_gaps = {g['gap_id'] for g in gaps(task, before)} | {g['gap_id'] for g in gaps(task)}
    counts = Counter(p.get('material_id') for p in proposals if isinstance(p,dict) and isinstance(p.get('material_id'),str))
    accepted, rejected = [], []
    for index, proposal in enumerate(proposals):
        try:
            check_schema(proposal, REVIEW)
            ident = proposal['material_id']
            if ident not in inventory or counts[ident] != 1:
                raise ValueError('Unknown, stale or duplicate material ID')
            if not proposal['reason'].strip():
                raise ValueError('Every material disposition needs an explicit reason')
            if not set(proposal['node_ids']) <= set(nodes) or not set(proposal['gap_ids']) <= known_gaps:
                raise ValueError('Review links must refer to retained nodes and current/prior gap IDs')
            own = set(inventory[ident]['reference_ids'])
            refs = set(proposal['evidence_ids'])
            if not refs <= own & inspected:
                raise ValueError('Review references must be inspected spans of this exact material')
            disposition = proposal['disposition']
            if disposition != 'deferred' and not refs:
                raise ValueError('A non-deferred review requires a supplied original-text reference')
            related = set(proposal['related_material_ids'])
            if ident in related or not related <= set(inventory):
                raise ValueError('Related material must be a different current saved source version')
            bound = [n for i,n in nodes.items() if i in proposal['node_ids'] and n['kind']=='observation'
                     and refs & {r['evidence_id'] for r in n.get('bindings', [])}]
            if disposition in {'incorporated','conflict'} and not bound:
                raise ValueError('Incorporation requires a retained observation bound to this source')
            if disposition == 'conflict':
                observations = [nodes[i] for i in proposal['node_ids'] if nodes[i]['kind']=='observation']
                if len(observations) < 2 or len({r['body_sha256'] for n in observations for r in n['bindings']}) < 2:
                    raise ValueError('Conflict requires both separately bound observations')
            if disposition == 'duplicate' and not related:
                raise ValueError('A duplicate receipt must identify its related saved source')
            if disposition in {'duplicate','irrelevant','deferred'} and proposal['effect'] != 'no_change' and proposal['effect'] != 'unknown':
                raise ValueError('Unincorporated material cannot claim directional forecast support')
            accepted.append({**copy.deepcopy(proposal),
                'source':{k:v for k,v in inventory[ident].items() if k != 'reference_ids'},
                'meaning_verified':False})
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'section':'material_reviews', 'index':index, 'error':str(exc), 'entry_sha256':digest(proposal)})
    change = graph_delta(before, current)
    event = {'map_revision':task.bundle['research_loop']['revision'],
        'map_event_sha256':task.bundle['research_loop']['events'][-1]['event_sha256'] if task.bundle['research_loop']['events'] else None,
        'material_sha256':material['material_sha256'], 'accepted_reviews':accepted,
        'rejected_reviews':rejected, 'actual_graph_delta':change,
        'model_explanation':explanation, 'explanation_verified':False,
        'submitted_proposal_sha256':result['acceptance'].get('submitted_proposal_sha256'),
        'quote_format_bindings':copy.deepcopy(result['acceptance'].get('quote_format_bindings',[])),
        'program_explanation':explain_applied(change,accepted,rejected,result['acceptance']),
        'at_utc':state.now(), 'previous_event_sha256':ledger['events'][-1]['event_sha256'] if ledger['events'] else None}
    # Repeated identical review-only replies cannot fill the journal or renew work.
    signature = digest({k:event[k] for k in ('map_revision','material_sha256','accepted_reviews',
        'rejected_reviews','actual_graph_delta','model_explanation','program_explanation')})
    repeated = any(e.get('review_signature') == signature for e in ledger['events'])
    if not repeated and (accepted or rejected or result.get('committed') or result['acceptance'].get('rejected')):
        if len(ledger['events']) >= event_cap(task):
            raise ValueError('Gap feedback lifetime receipt cap exhausted')
        event['review_signature'] = signature
        event['event_sha256'] = digest(event)
        ledger['events'].append(event)
    remaining = pending(task, inventory)
    result['gap_feedback'] = {**event, 'cached_review':repeated,
        'pending_material_count':len(remaining), 'material_acknowledged':not remaining,
        'scope':'Mechanical bindings and declared dispositions only; no verified causal meaning or gap closure.'}
    result['acceptance'].setdefault('rejected', []).extend(rejected)
    if rejected:
        result['acceptance']['status'] = 'partial'
    result['material_acknowledged'] = not remaining
    task.bundle['research_acquisition']['pending_map_update'] = bool(remaining)
    return result
