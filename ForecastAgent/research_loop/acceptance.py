"""Node isolation for experimental maps; envelope, identity and caps stay atomic."""
import copy
from collections import Counter

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.research_loop.schema import PROPOSAL, RELATION, NEED
from ForecastAgent.research_loop.state import enabled, initialize, catalog, bind_node, update

POLICY = 'node-isolation-v2'
ENVELOPE = copy.deepcopy(PROPOSAL)
for _field in ('nodes', 'relations', 'material_requests'):
    ENVELOPE['properties'][_field]['items'] = {}


class MapAcceptanceError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__('No accepted original-text observation or explicitly unknown-only gap inventory; ' +
                         '; '.join(r['error'] for r in report['rejected'])[:1200])


def accept(bundle, proposal, cutoff=None, allowed=None, *, map_protocol='legacy', score_plan=None, stage_grounding=False, preserve_rejected=False):
    """Keep valid nodes without repairing model claims or refreshing any quota."""
    if not enabled(bundle):
        raise ValueError('Research state policy is disabled')
    envelope=ENVELOPE
    if preserve_rejected:
        # A bounded patch plus old entries may exceed the final map cap before
        # invalid additions are isolated. Final update still checks strict caps.
        envelope=copy.deepcopy(ENVELOPE)
        for field in ('nodes','relations','material_requests'):
            envelope['properties'][field]['maxItems']*=2
    check_schema(proposal, envelope)
    candidate = copy.deepcopy(bundle)
    ledger = initialize(candidate)
    from ForecastAgent.research_loop import simple_map
    if map_protocol not in {'legacy'} | simple_map.PROTOCOLS:
        raise ValueError('Unknown map protocol')
    simple = map_protocol in simple_map.PROTOCOLS
    raw_hash = digest(proposal)
    context_hash = digest(sorted(allowed) if allowed is not None else None)
    previous = ledger['events'][-1].get('acceptance') if ledger['events'] else None
    from ForecastAgent.research_loop.grounding import LABEL_POLICY
    if (previous and previous['proposal_sha256'] == raw_hash and previous['allowed_sha256'] == context_hash
            and previous.get('map_protocol', 'legacy') == map_protocol
            and previous.get('stage_grounding', False) == stage_grounding
            and previous.get('preserve_rejected', False) == preserve_rejected
            and (not stage_grounding or previous.get('label_grounding_policy') == LABEL_POLICY)
            and (map_protocol != 'forecast-score-map-v4' or
                 previous.get('score_plan', {}).get('proposal_sha256') == digest(score_plan))):
        # Recheck material identity even for a cached update.
        if catalog(candidate, cutoff)['material_sha256'] != proposal['material_sha256']:
            raise ValueError('Material changed since inspection')
        return {'revision': ledger['revision'], 'cached': True, 'no_progress': True,
                'acceptance': copy.deepcopy(previous)}
    if proposal['expected_revision'] != ledger['revision']:
        raise ValueError('Stale research revision; inspect the current state')
    if ledger['revision'] >= ledger['update_cap']:
        raise ValueError('Research lifetime update cap exhausted')
    material = catalog(candidate, cutoff)
    if proposal['material_sha256'] != material['material_sha256']:
        raise ValueError('Material changed since inspection')
    if not simple and any(not proposal[k].strip() for k in ('supporting_path', 'alternative_path', 'revision_reason')):
        raise ValueError('Both paths and the revision reason must be explicit')
    old = {n['id'] for n in (ledger['current'] or {}).get('nodes', [])}
    supplied_ids = {n.get('id') for n in proposal['nodes'] if isinstance(n, dict) and isinstance(n.get('id'), str)}
    retired = proposal['retired_node_ids']
    if len(set(retired)) != len(retired) or set(retired) != old - supplied_ids:
        raise ValueError('Every removed node must be explicitly retired')
    counts = Counter(n.get('id') for n in proposal['nodes'] if isinstance(n, dict) and isinstance(n.get('id'), str))
    # Failed observations must not become a gap-only map by quarantine alone.
    intentional_gaps = all(isinstance(n, dict) and n.get('kind') == 'unknown'
                           for n in proposal['nodes'])
    accepted, rejected, nodes = [], [], []
    retained_prior=[]
    previous_nodes={n['id']:n for n in (ledger['current'] or {}).get('nodes',[])}
    def reject(section, index, entry, error):
        rejected.append({'section': section, 'index': index,
            'node_id': entry.get('id') if isinstance(entry, dict) else None,
            'entry_sha256': digest(entry), 'error': str(error)})
    for index, original in enumerate(proposal['nodes']):
        try:
            if isinstance(original, dict) and isinstance(original.get('id'), str) and counts[original['id']] > 1:
                raise ValueError('Duplicate node ID; every occurrence is quarantined')
            chosen = copy.deepcopy(original)
            scope = set(allowed) if allowed is not None else None
            if stage_grounding and isinstance(chosen, dict):
                from ForecastAgent.research_loop.grounding import isolate_labels
                chosen, fields = isolate_labels(chosen, material, scope)
                rejected.extend(fields)
            if simple:
                check_schema(chosen, simple_map.NODE_SCHEMA)
            if map_protocol in {'forecast-map-v3', 'forecast-score-map-v4'}:
                from ForecastAgent.research_loop.forecast_map import bind_observation
                bound, chosen, fields = bind_observation(chosen, material, scope)
                rejected.extend(fields)
            else:
                bound = bind_node(chosen, material, scope, validate_stage=stage_grounding)
            nodes.append(chosen); accepted.append(bound['id'])
        except (ValueError, TypeError, KeyError) as exc:
            reject('nodes', index, original, exc)
            ident=original.get('id') if isinstance(original,dict) else None
            if preserve_rejected and ident in previous_nodes and counts[ident]==1:
                from ForecastAgent.research_loop.delta import node_input
                prior=node_input(previous_nodes[ident])
                try:
                    bind_node(prior,material,allowed,validate_stage=stage_grounding)
                except (ValueError,TypeError,KeyError):
                    pass  # Stale old evidence cannot be rescued as a valid node.
                else:
                    nodes.append(prior);accepted.append(ident);retained_prior.append(ident)
    keep = set(accepted)
    relations, needs = [], []
    for section, schema, target in [('relations', RELATION, relations), ('material_requests', NEED, needs)]:
        for index, entry in enumerate(proposal[section]):
            try:
                check_schema(entry, simple_map.RELATION_SCHEMA if simple and section == 'relations' else schema)
                if section == 'relations':
                    if intentional_gaps:
                        raise ValueError('A gap-only inventory cannot establish relationships')
                    if not {entry['from_id'], entry['to_id']} <= keep or not entry['rationale'].strip():
                        raise ValueError('Relation endpoint unavailable or rationale empty')
                    if simple:
                        simple_map.validate_relation(entry, ledger['rules'])
                elif (not set(entry['node_ids']) <= keep or not entry['target'].strip() or not entry['reason'].strip()):
                    raise ValueError('Material request node unavailable or target/reason empty')
                target.append(copy.deepcopy(entry))
            except (ValueError, TypeError, KeyError) as exc:
                reject(section, index, entry, exc)
    report = {'policy': POLICY, 'proposal_sha256': raw_hash, 'allowed_sha256': context_hash,
        'preserve_rejected':preserve_rejected,
        'accepted_node_ids': accepted, 'proposed_nodes': len(proposal['nodes']), 'rejected': rejected,
        'narratives_omitted': bool(rejected), 'meaning_verified': False,
        'factual_grounding_present': any(n['kind'] == 'observation' for n in nodes),
        'status': 'partial' if rejected else 'accepted',
        'retained_prior_nodes_after_rejected_replacement':retained_prior,
        'automatically_retired_quarantined_nodes': sorted((old & supplied_ids) - keep)}
    if stage_grounding:
        report['stage_grounding'] = True
        report['label_grounding_policy'] = LABEL_POLICY
    if simple:
        report['map_protocol'] = map_protocol
        report['empty_narratives_defaulted'] = [k for k in ('supporting_path', 'alternative_path', 'revision_reason')
            if not proposal[k].strip()]
    gap_only = intentional_gaps and bool(nodes)
    if not report['factual_grounding_present'] and not gap_only:
        report['status'] = 'no_grounded_map'
        raise MapAcceptanceError(report)
    if gap_only:
        report.update(status='gap_only', narratives_omitted=True)
    from ForecastAgent.research_loop import delta
    if delta.enabled(bundle) and not delta.content_changed(ledger['current'],nodes,relations,needs):
        report.update(status='rejected_no_change' if rejected else 'unchanged', material_acknowledged=False)
        return {'revision':ledger['revision'],'cached':False,'committed':False,'no_progress':True,
            'acceptance':report,'requires_local_read':bool(rejected),
            'instruction':'Accepted graph is unchanged. Rejected claims do not acknowledge saved material. Inspect a substantive source window before correcting the patch.'}
    sanitized = copy.deepcopy(proposal)
    sanitized.update(nodes=nodes, relations=relations, material_requests=needs, retired_node_ids=sorted(old - keep))
    if simple:
        # Narrative is optional in a small map. Neutral bookkeeping defaults do
        # not alter quotes, add observations or repair a model interpretation.
        defaults = {'supporting_path': 'Evaluate support from the accepted nodes and original text.',
            'alternative_path': 'Evaluate alternatives from original text; gaps are not negative evidence.',
            'revision_reason': 'Record the source-first map over frozen original evidence.'}
        for key, default in defaults.items():
            if not sanitized[key].strip():
                sanitized[key] = default
    if rejected or gap_only:
        # Free prose might repeat quarantined claims. It stays only in the raw audit.
        sanitized.update(supporting_path='Supporting interpretation must be rechecked from accepted nodes and original text.',
            alternative_path='Alternative interpretation must be rechecked from original text; omissions are not negative evidence.')
    result = update(candidate, sanitized, cutoff)
    if result.get('cached'):
        return {**result, 'acceptance': report}
    event = candidate['research_loop']['events'][-1]
    if map_protocol == 'forecast-score-map-v4':
        from ForecastAgent.research_loop.conditional import plan_audit
        report['score_plan'] = plan_audit(score_plan, candidate['research_loop']['current']['nodes'])
    event['acceptance'] = report
    event['event_sha256'] = digest({k: v for k, v in event.items() if k != 'event_sha256'})
    bundle['research_loop'] = candidate['research_loop']
    return {**result, 'committed':True, 'event_sha256': event['event_sha256'], 'acceptance': copy.deepcopy(report)}
