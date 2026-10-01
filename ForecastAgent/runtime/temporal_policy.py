"""Separate acquisition permissions from immutable question date provenance."""
from copy import deepcopy
from datetime import datetime, timezone

CURRENT = 'current_information'
ENFORCED = 'cutoff_enforced'
WARNING = 'Current information is permitted; this package is not a cutoff-safe historical backtest.'


def policy(bundle):
    value = bundle.get('collection_temporal_policy', bundle.get('request', {}).get('collection_temporal_policy', ENFORCED))
    if value not in {CURRENT, ENFORCED}:
        raise ValueError('Invalid collection temporal policy')
    return value


def unrestricted(bundle):
    return policy(bundle) == CURRENT


def configure(bundle):
    """Apply effective permissions without rewriting the frozen request."""
    value = policy(bundle)
    bundle['collection_temporal_policy'] = value
    if value == CURRENT:
        if bundle.get('pipeline') != 'collection':
            raise ValueError('Current-information policy requires the collection pipeline')
        bundle['mode'] = 'live'
        bundle['end_date'] = None
        bundle['historical_clean'] = False
        bundle['temporal_warning'] = WARNING


def amend_current(bundle, reason):
    """Explicit one-way amendment; preserve attempts, quotas and prior exports."""
    if not reason.strip():
        raise ValueError('A recorded operator reason is required')
    if unrestricted(bundle):
        return False
    if bundle.get('pipeline') != 'collection':
        raise ValueError('Only collection tasks can change acquisition permissions')
    at = datetime.now(timezone.utc).isoformat()
    bundle.setdefault('temporal_policy_amendments', []).append({
        'at_utc': at, 'from': policy(bundle), 'to': CURRENT, 'reason': reason,
        'request_hash': bundle['request_hash'],
        'original_as_of_utc': bundle['request'].get('as_of_utc'),
        'limits': deepcopy(bundle['acquisition_limits']),
        'tavily_attempts': len(bundle.get('searches', [])),
        'exa_attempts': len(bundle.get('exa_searches', [])),
        'model_attempts': len(bundle.get('model_attempts', []))})
    if bundle.get('result'):
        bundle.setdefault('result_history', []).append({'at_utc': at, 'reason': reason,
            'collection_temporal_policy': ENFORCED, 'result': deepcopy(bundle['result'])})
    bundle['collection_temporal_policy'] = CURRENT
    configure(bundle)
    # The new collection policy permits another session, never another quota.
    bundle['result'] = None
    control = bundle.setdefault('control', {})
    control.update(forced_close=False, consecutive_errors=0, no_progress_turns=0, seen_calls=[])
    # Saved rejected captures remain immutable in quarantine; grant reading
    # permission through a separate page entry, without new HTTP requests.
    from ForecastAgent.tavily_research import canonical_url
    for entry in bundle.get('quarantine', []):
        page = entry.get('page_snapshot')
        url = entry.get('url') or (page or {}).get('url')
        if page and url:
            bundle.setdefault('pages', {}).setdefault(canonical_url(url), deepcopy(page))
    bundle['session_state'] = 'pending'
    return True
