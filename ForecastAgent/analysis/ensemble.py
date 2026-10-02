"""Deterministic two-route comparison; shared analysis is explicitly dependent."""
from ForecastAgent.providers.decisions import probability


def compare(reasoning, mercury=None):
    reasoning = probability(reasoning)
    mercury = probability(mercury) if mercury is not None else None
    return {
        'schema': 'two_route_comparison_v1',
        'routes': {
            'reasoning_only': {'probability_yes': reasoning, 'status': 'available'},
            'reasoning_then_mercury': {'probability_yes': mercury,
                                       'status': 'available' if mercury is not None else 'unavailable'},
        },
        'equal_mean_probability_yes': (reasoning + mercury) / 2 if mercury is not None else None,
        'absolute_disagreement': abs(reasoning - mercury) if mercury is not None else None,
        'independent_forecasters': False,
        'dependency': 'Mercury consumes the reasoning report but not its numeric probability.',
        'calibration': {'method': 'identity', 'fitted': False},
        'automatic_use_eligible': False,
    }


def metrics(p, y):
    import math
    p, y = probability(p), probability(y)
    if y not in (0, 1):
        raise ValueError('Binary outcome required')
    clipped = max(1e-6, min(1 - 1e-6, p))
    return {'brier': (p - y) ** 2,
            'log_loss': -(y * math.log(clipped) + (1 - y) * math.log(1 - clipped)),
            'correct_at_half': (p >= .5) == bool(y)}
