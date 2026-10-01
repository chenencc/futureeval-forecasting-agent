"""Audited acquisition-need lifecycle without changing the frozen plan."""
from copy import deepcopy
from datetime import datetime, timezone


def active_needs(bundle):
    statuses = bundle.get('need_status', {})
    return [n for n in bundle.get('plan') or []
            if statuses.get(n['id'], {}).get('status', 'active') == 'active']


def set_status(task, args):
    from ForecastAgent.runtime.contracts import ContractError
    from ForecastAgent.runtime.task_protocol import task_view
    ident, status, reason = args.get('need_id'), args.get('status'), args.get('reason')
    known = {n['id'] for n in task.bundle.get('plan') or []}
    if ident not in known or status not in {'active', 'not_applicable', 'deferred'} or not isinstance(reason, str) or len(reason.strip()) < 20:
        raise ContractError('invalid_need_status', 'arguments',
            'Use an existing need ID and a substantive reason. Only useful background needs may be deferred; failed retrieval never establishes inapplicability.')
    need = next(n for n in task.bundle['plan'] if n['id'] == ident)
    if status == 'deferred' and need['priority'] == 'critical':
        raise ContractError('critical_deferral', 'status', 'Critical acquisition cannot be deferred to manufacture completion. Preserve its concrete gap.')
    statuses = task.bundle.setdefault('need_status', {})
    proposed = {**statuses, ident: {'status': status}}
    if not any(n['priority'] == 'critical' for n in active_needs({**task.bundle, 'need_status': proposed})):
        raise ContractError('empty_critical_scope', 'status', 'Keep at least one active critical acquisition need. Do not retire the task objective.')
    if statuses.get(ident, {}).get('status') == status:
        return {'need_id': ident, 'status': status, 'cached': True}
    event = {'need_id': ident, 'status': status, 'reason': reason.strip(),
        'previous': deepcopy(statuses.get(ident, {'status': 'active'})),
        'task_protocol': task_view(task), 'at_utc': datetime.now(timezone.utc).isoformat(),
        'truth_verified': False, 'scope': 'Agent-declared acquisition applicability, not an outcome or factual verdict.'}
    task.bundle.setdefault('need_status_events', []).append(event)
    statuses[ident] = event
    return {'need_status': event, 'active_need_ids': [n['id'] for n in active_needs(task.bundle)]}


def reconciliation(task):
    from ForecastAgent.runtime.task_protocol import validate_current_plan
    from ForecastAgent.runtime.contracts import ContractError
    issues = []
    for need in active_needs(task.bundle):
        try:
            validate_current_plan(task, [need])
        except ContractError as exc:
            issues.append({'need_id': need['id'], 'issue': exc.details,
                'instruction': 'Reconsider this frozen need against the operating clock. If it no longer applies, explicitly set its acquisition status with a reason; do not invent material or silently alter the original plan.'})
    return issues


def inventory(bundle):
    """Describe durable material associations, never semantic sufficiency or truth."""
    from ForecastAgent.evidence.acceptance import acquisition_metrics
    from ForecastAgent.readers.datasets import observation_range
    metrics = acquisition_metrics(bundle)
    sources = {r['url']:r for r in metrics['sources']}
    rows = []
    for need in metrics['needs']:
        excerpts = [e for e in bundle.get('excerpts',[]) if need['need_id'] in e.get('need_ids',[])]
        reads = [r for r in bundle.get('dataset_reads',{}).values()
                 if need['need_id'] in r.get('need_ids',[]) and r['url'] in need['usable_associated_sources']]
        rows.append({**need, 'excerpt_ids':[e['id'] for e in excerpts],
            'delivered_dataset_views':reads,
            'material_state':'excerpt_saved' if excerpts else 'rows_delivered' if reads else 'body_associated' if need['usable_associated_sources'] else 'no_associated_material',
            'source_handles':[{'url':url,**observation_range(bundle['pages'][url])}
                for url in need['usable_associated_sources'] if url in sources],
            'semantic_adequacy_verified':False})
    return {'schema':'acquisition_inventory_v1', 'needs':rows,
        'unassociated_saved_datasets':[{'url':url,**observation_range(page)}
            for url,page in bundle.get('pages',{}).items() if page.get('rows') and not any(url in n['usable_associated_sources'] for n in rows)],
        'instruction':'These are durable acquisition associations, not truth or semantic adequacy. Do not claim no source was captured for a need with saved bodies/excerpts. Describe the exact missing period, paragraph, identity or authority instead.'}
