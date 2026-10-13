"""Future-price adapter on the current development original/map scoring pair."""
import copy
import hashlib
from pathlib import Path

from . import PROTOCOL, ENGINE_BRANCH, ENGINE_SHA
from .tracking import creation_schedule, schedule
from .common import digest, instant, now, number, read, write

GRID = [i / 20 for i in range(21)]


def registry(event_heads, hours, schedule_=None):
    result = copy.deepcopy(event_heads)
    for horizon in hours:
        timing = ('market_created_at plus ' + str(horizon) + ' hours, at exact UTC target ' + schedule_['targets'][str(horizon)]) if schedule_ and schedule_['origin_role'] == 'market_created_at' else ('anchor_at plus ' + str(horizon) + ' hours')
        result['price_' + str(horizon)] = {
            'type': 'choice',
            'instructions': 'Predict the YES bid/ask midpoint at ' + timing +
                ', conditional on the contract still being open and orderable. These are PRICE GRID '
                'representative points, not mutually independent event probabilities. Give a normalized '
                'predictive distribution. Use anchor quote and information frozen at anchor_at only. '
                'Wide-spread midpoints are noisy book measurements, not reliable consensus or executable prices. '
                'Consider what information traders will learn during this interval. Do not substitute '
                'eventual resolution probability for future market price. Research-map explanations '
                'are unverified interpretations; supplied original evidence takes precedence.',
            'criteria': {'p' + str(i): 'YES midpoint represented by ' + format(p, '.2f') for i, p in enumerate(GRID)}}
    return result


def mean(answer):
    values = answer['probabilities']
    if set(values) != {'p' + str(i) for i in range(len(GRID))}:
        raise ValueError('Missing horizon grid bins')
    mass = sum(number(x) for x in values.values())
    if any(not 0 <= number(v) <= 1 for v in values.values()) or abs(mass - 1) > .02 or mass <= 0:
        raise ValueError('Invalid horizon probability mass')
    return sum(p * number(values['p' + str(i)]) for i, p in enumerate(GRID)) / mass


def prepare(bundle, anchor, cfg, market=None):
    from .materials import prepare as admit
    from ForecastAgent.research_loop import prospective_trial
    from ForecastAgent.research_loop.forecast_brief import registry as event_registry
    from ForecastAgent.research_loop.target_pack import pack
    from unittest.mock import patch
    from ForecastAgent.research_loop.decision import BYTE_CAP
    from ForecastAgent.analysis.mercury_evidence_chain import request_bytes
    view, admission = admit(bundle)
    if not view.get('pages'):
        raise ValueError('Development admission found no usable original bodies')
    event_heads, spec = event_registry(bundle['request'])
    price_schedule = cfg.get('price_schedule') or (creation_schedule({'market': market}) if cfg.get('price_target_origin') == 'market_created_at' else schedule(anchor['received_at'], cfg['horizons_hours'], 'forecast_anchor_at'))
    future_hours = [h for h in cfg['horizons_hours'] if instant(anchor['received_at']) < instant(price_schedule['targets'][str(h)])]
    heads = registry(event_heads, future_hours, price_schedule)
    metadata = {'price_schedule': price_schedule, 'anchor_at': anchor['received_at'], 'anchor_quote': anchor,
                'target_policy': {'horizons_hours': cfg['horizons_hours'],
                    'label_window_minutes': cfg['label_window_minutes'],
                    'target': 'observed_yes_bid_ask_midpoint', 'eventual_event_is_separate': True,
                    'anchor_quality_flags': anchor.get('quality_flags', [])}}
    # Only the packing seam receives a smaller declared cap. All current
    # bound-source, map grounding, literal visibility and scope checks still
    # run inside the real development prepared_pair implementation.
    def market_pack(projected, unused_event_heads, **kwargs):
        return pack(projected, heads, limit=20000, **kwargs)
    with patch.object(prospective_trial, 'pack', market_pack):
        common, pair, audit = prospective_trial.prepared_pair(bundle, original_view=view)
    states = {arm: copy.deepcopy(pair[arm]['state']) | copy.deepcopy(metadata) for arm in ('original', 'mapped')}
    if request_bytes(states['mapped'], heads) > BYTE_CAP:
        states['mapped'] = copy.deepcopy(states['original'])
        audit.update(map_delivered=False, map_omission_reason='future_price_heads_exceed_context_cap')
    if any(request_bytes(s, heads) > BYTE_CAP for s in states.values()):
        raise ValueError('Matched originals, map and future-price heads exceed development context limit')
    if states['original']['evidence'] != states['mapped']['evidence'] or states['original']['sources'] != states['mapped']['sources']:
        raise ValueError('Development scoring arms do not share identical original coverage')
    for key, value in common.items():
        if any(s[key] != value for s in states.values()):
            raise ValueError('Market adapter altered development common originals')
    audit.update(map_delivered=bool(states['mapped'].get('research_map')),
                 admission=admission, same_originals=True, same_price_heads=True,
                 missing_map_is_explicit_original_only_fallback=True)
    return states, heads, audit


def run(bundle, market, anchor, root, cfg):
    from ForecastAgent.research_loop import decision_http
    from ForecastAgent.intelligence.identity import code_identity
    root = Path(root)
    states, heads, audit = prepare(bundle, anchor, cfg, market)
    identity = {'protocol': PROTOCOL, 'target_adapter': 'development-map-price-grid-v3',
                'engine_branch': ENGINE_BRANCH, 'engine_sha': ENGINE_SHA, 'implementation': code_identity(),
                'adapter_sha256_lf': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                'bundle_sha256': digest(bundle), 'states_sha256': digest(states),
                'registry_sha256': digest(heads), 'policy_sha256': digest(cfg)}
    if (root / 'identity.json').exists() and read(root / 'identity.json') != identity:
        raise ValueError('Frozen development market forecast identity changed')
    if (root / 'result.json').exists():
        saved = read(root / 'result.json')
        if saved.get('identity') != identity:
            raise ValueError('Development result identity changed')
        return saved
    write(root / 'identity.json', identity)
    write(root / 'pair-audit.json', audit)
    write(root / 'questions.json', heads)
    arms = {}
    for arm in cfg['analysis_arms']:
        if arm not in states:
            raise ValueError('Unsupported development analysis arm')
        folder = root / arm
        write(folder / 'state.json', states[arm])
        if arm == 'mapped' and not audit['map_delivered']:
            arms[arm] = {'status': 'not_comparable', 'reason': 'accepted_map_unavailable',
                         'map_delivered': False, 'new_model_requests': 0}
            write(folder / 'result.json', arms[arm])
            continue
        try:
            response = decision_http.call(states[arm], folder / 'decision', heads, required_questions=heads)
            value = {'status': 'completed', 'price_means': {str(h): mean(response['answers']['price_' + str(h)]) for h in cfg['horizons_hours'] if 'price_' + str(h) in heads},
                     'eventual_probability_yes': min(.98, max(.02, response['answers']['event_yes']['noul'])),
                     'event_probability_is_not_future_price': True,
                     'map_delivered': arm == 'mapped' and audit['map_delivered'],
                     'map_status': 'delivered' if arm == 'mapped' and audit['map_delivered'] else 'original_only',
                     'conditional_reread': False, 'response_sha256': digest(response)}
            write(folder / 'result.json', value); arms[arm] = value
        except Exception as exc:
            arms[arm] = {'status': 'failed', 'error_type': type(exc).__name__,
                         'original_provider_reservation_preserved': True, 'release_fallback': False}
            write(folder / 'failure.json', arms[arm])
    sealed_at = now()
    latency = (instant(sealed_at) - instant(anchor['received_at'])).total_seconds() / 60
    timely = 0 <= latency <= cfg['maximum_seal_latency_minutes']
    price_schedule = states['original']['price_schedule']
    result = {'price_schedule': price_schedule,
              'omitted_elapsed_price_heads': [h for h in cfg['horizons_hours'] if 'price_' + str(h) not in heads],
              'forecast_before_targets': {h: instant(sealed_at) < instant(t) for h, t in price_schedule['targets'].items()},
              'anchor_at': anchor['received_at'], 'anchor': anchor, 'sealed_at': sealed_at,
              'seal_latency_minutes': latency, 'timely': timely,
              'baseline': {str(h): anchor['midpoint'] for h in cfg['horizons_hours']},
              'arms': arms, 'identity': identity, 'pair_audit': audit,
              'status': 'completed' if timely and any(a['status'] == 'completed' for a in arms.values()) else 'incomplete',
              'no_orders_or_platform_forecasts': True, 'development_only': True, 'release_fallback': False}
    write(root / 'result.json', result)
    return result
