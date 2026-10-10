"""Stable experimental conversion; provider probabilities retain strict validation."""
import math

from ForecastAgent.analysis.distributions import (probability, grid, interpolate,
    standardize_cdf, clip_categories)

ROUNDING_EPSILON = 1e-12


def forecast(response, spec):
    original = response['answers']['event_outcome']['probabilities']
    if set(original) != set(spec['criteria']):
        raise ValueError('Outcome probabilities do not match the exact supplied intervals')
    # No tolerance on provider values: only derived cumulative arithmetic is bounded.
    values = {key: probability(value) for key, value in original.items()}
    total = math.fsum(values.values())
    if total <= 0 or abs(total - 1) > .02:
        raise ValueError('Outcome probabilities do not sum to one')
    probabilities = {key: value / total for key, value in values.items()}
    adjustments = []
    def derived_probability(value, location):
        if not math.isfinite(value) or value < -ROUNDING_EPSILON or value > 1 + ROUNDING_EPSILON:
            raise ValueError('Derived cumulative probability outside rounding tolerance')
        bounded = min(1., max(0., value))
        if bounded != value:
            adjustments.append({'location': location, 'original': value, 'bounded': bounded})
        return bounded
    arithmetic = {'policy': 'fsum-derived-cdf-v1', 'raw_sum': total,
        'raw_values_strictly_validated': True, 'rounding_epsilon': ROUNDING_EPSILON,
        'derived_rounding_adjustments': adjustments}
    if spec['kind'] == 'multiple_choice':
        options = {option: probabilities[f'option_{i}'] for i, option in enumerate(spec['options'])}
        option_sum = math.fsum(options.values())
        options = {key: value / option_sum for key, value in options.items()}
        return {'probabilities': options, 'clipped_probabilities': clip_categories(options, spec['options']),
            'top_option': max(options, key=options.get), 'payload_format_valid': True,
            'arithmetic_audit': arithmetic}
    edges = spec['edges']
    terms = [probabilities.get('below', 0.)]
    knots = [[edges[0], derived_probability(math.fsum(terms), 'lower_edge')]]
    for i in range(len(edges) - 1):
        terms.append(probabilities[f'bin_{i}'])
        knots.append([edges[i+1], derived_probability(math.fsum(terms), f'edge_{i+1}')])
    result = {'bin_probabilities': probabilities, 'cdf_knots': knots,
        'payload_format_valid': False, 'official_metadata_available': spec['official_metadata_available'],
        'arithmetic_audit': arithmetic}
    if spec['meta'] is not None:
        meta = spec['meta']; full = grid(meta)
        positions = [full.index(edge) for edge in edges]
        raw = interpolate(list(zip(positions, [p for _, p in knots])), list(range(len(full))))
        raw = [derived_probability(value, f'grid_{i}') for i, value in enumerate(raw)]
        result.update(raw_cdf=raw, continuous_cdf=standardize_cdf(raw, meta), payload_format_valid=True)
    else:
        result['payload_gap'] = 'Date API question identity and scale metadata missing; research distribution only.'
    return result
