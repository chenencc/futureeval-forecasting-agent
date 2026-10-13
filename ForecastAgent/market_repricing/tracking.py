"""Explicit market-creation observations and immutable legacy forecast targets."""
from datetime import timedelta

from .common import instant

CREATION = 'market-creation-price-targets-v1'
LEGACY = 'forecast-anchor-price-targets-v2'


def schedule(origin, hours, role):
    stamp = instant(origin)
    return {'schema': CREATION if role == 'market_created_at' else LEGACY,
            'origin_role': role, 'origin_at': stamp.isoformat(),
            'targets': {str(h): (stamp + timedelta(hours=h)).isoformat() for h in hours}}


def creation_schedule(task):
    # The immutable enrollment market supplies create time, never refreshed metadata.
    return schedule(task['market']['created_at'], (24, 72, 168), 'market_created_at')


def forecast_schedule(forecast):
    if forecast.get('price_schedule'):
        return forecast['price_schedule']
    # Previously sealed heads retain their original anchor semantics and horizons.
    hours = [int(h) for h in forecast.get('baseline', {})] or [72, 168]
    return schedule(forecast['anchor_at'], hours, 'forecast_anchor_at')


def labels(schedule_, market, quotes, clock, cfg):
    result = {}
    for hours, stamp in schedule_['targets'].items():
        target = instant(stamp)
        if instant(clock) < target:
            continue
        base = {'target_at': target.isoformat(), 'origin_at': schedule_['origin_at'],
                'origin_role': schedule_['origin_role'], 'schedule_schema': schedule_['schema']}
        if market.get('rule_drift_seen_at') and instant(market['rule_drift_seen_at']) <= target:
            result[hours] = base | {'status': 'rules_changed', 'excluded_from_repricing_mae': True}
            continue
        # A known terminal event makes an open-market conditional price inapplicable.
        final = market.get('final_observation', {})
        terminal_at = final.get('resolved_at') or market.get('terminal_seen_at')
        if market.get('terminal_result') and terminal_at and instant(terminal_at) <= target:
            result[hours] = base | {'status': 'early_resolution',
                'terminal_value': 1 if market['terminal_result'] == 'yes' else 0,
                'excluded_from_repricing_mae': True}
            continue
        window = target - timedelta(minutes=cfg['label_window_minutes'])
        usable = [q for q in quotes if q.get('status') == 'usable' and
                  window <= instant(q['received_at']) <= target]
        if usable:
            q = max(usable, key=lambda q: instant(q['received_at']))
            result[hours] = base | {'status': 'quoted', 'quote': q, 'label': q['midpoint'],
                                    'window_minutes': cfg['label_window_minutes']}
        else:
            result[hours] = base | {'status': 'missing_window_quote',
                'excluded_from_repricing_mae': True, 'no_late_quote_substitution': True}
    return result


def targets_for(task, worker_task):
    targets = []
    if task.get('market', {}).get('created_at'):
        targets.extend(creation_schedule(task)['targets'].values())
    if worker_task.get('forecast'):
        targets.extend(forecast_schedule(worker_task['forecast'])['targets'].values())
    return [instant(t) for t in targets]


def final_record(market, frozen_rules_hash):
    final = dict(market.get('final_observation') or {'status': 'unresolved', 'result': None})
    final['rules_match_enrollment'] = market['rules_sha256'] == frozen_rules_hash and not market.get('rule_drift')
    final['eligible_for_event_brier'] = (final.get('status') == 'resolved' and
        final.get('result') in {'yes', 'no'} and final['rules_match_enrollment'])
    return final
