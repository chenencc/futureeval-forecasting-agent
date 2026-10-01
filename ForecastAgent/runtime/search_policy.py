"""Frozen search obligations, separate from attempt quotas and evidence quality."""
import os

from ForecastAgent.runtime.contracts import ContractError


def freeze(bundle, request, existing):
    if 'search_policy' not in bundle:
        default = 'optional' if existing or bundle['pipeline'] != 'collection' else 'required'
        selected = request.get('exa_search_policy', default) if not existing else default
        if selected not in {'required', 'optional'}:
            raise ValueError('exa_search_policy must be required or optional')
        bundle['search_policy'] = {'exa':selected, 'version':1}


def requirement(task):
    required = task.bundle.get('search_policy', {}).get('exa') == 'required'
    attempts = task.bundle.get('exa_searches', [])
    if attempts:
        status = attempts[-1].get('status', 'reserved')
        met = status in {'completed', 'failed'}
        state = 'attempted_'+status if met else 'interrupted_unknown'
        reason = 'One provider attempt consumed; source relevance and successful discovery are separate.' if met else 'Interrupted reservation is consumed but its HTTP outcome is unknown; do not retry.'
    elif not task.exa_limit:
        met, state, reason = False, 'unavailable', 'Frozen Exa allowance is zero; no implicit budget grant.'
    elif not os.environ.get('EXA_API_KEY'):
        met, state, reason = False, 'unavailable', 'EXA_API_KEY is not configured.'
    else:
        met, state, reason = False, 'pending', 'Ultra must select one task-grounded supplemental discovery query.'
    return {'required':required, 'attempt_requirement_met':met,
            'status':state if required else 'optional', 'provider_attempts':len(attempts),
            'reason':reason, 'truth_verified':False}


def ready(task):
    obligation = requirement(task)
    return obligation['required'] and obligation['status'] == 'pending'


def before_finish(task):
    obligation = requirement(task)
    if ready(task) and not task.bundle['control'].get('forced_close'):
        raise ContractError('required_search_pending', 'tool',
                            'Complete the required single Exa discovery attempt before normal finish. Choose a concrete task query and existing need IDs.', ['search_exa'])
    return obligation
