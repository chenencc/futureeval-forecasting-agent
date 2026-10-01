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
    if ident not in known or status not in {'active', 'not_applicable'} or not isinstance(reason, str) or len(reason.strip()) < 20:
        raise ContractError('invalid_need_status', 'arguments',
            'Use an existing need ID, active/not_applicable and a substantive context-based reason. Failed retrieval or lack of evidence cannot establish inapplicability.')
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
