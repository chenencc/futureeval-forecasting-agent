"""Separate bounded collection closure from interruptions and repair readiness."""

CLOSED_STOPS = {'program_dispatch_limit', 'model_dispatch_budget', 'stalled',
                'program_forced_close', 'repeated_tool_errors', 'review_round_limit'}


def execution_state(bundle):
    result = bundle.get('result') or {}
    execution = result.get('execution_report') or {}
    reason = result.get('termination_reason') or execution.get('stop_reason')
    interrupted = execution.get('interrupted', bool(result.get('incomplete')))
    return {'interrupted': bool(interrupted), 'resumable': bool(result.get('resumable')),
            'termination_reason': reason,
            'halt_kind': 'interrupted' if interrupted else
                         'bounded_closure' if reason in CLOSED_STOPS else 'finished'}


def supplement_ready(bundle):
    """Closed observed leads may be repaired; no body adequacy is inferred."""
    result = bundle.get('result') or {}
    execution = result.get('execution_report') or {}
    if (result.get('termination_reason') not in CLOSED_STOPS or
            execution.get('owner') != 'program' or execution.get('interrupted') is not False or
            result.get('status') not in {'collected', 'leads_only', 'empty'}):
        return False
    attempts = bundle.get('model_attempts') or []
    if not attempts or any(a.get('status') != 'received' for a in attempts):
        return False  # Unknown receipts and transport failures require recovery first.
    from ForecastAgent.runtime.source_frontier import unread_candidates
    return bool(bundle.get('fetch_attempts') or unread_candidates(bundle))


def has_readable_material(bundle):
    return any(p.get('content') and p.get('body_diagnostics', {}).get('usable_text')
               for p in bundle.get('pages', {}).values())
