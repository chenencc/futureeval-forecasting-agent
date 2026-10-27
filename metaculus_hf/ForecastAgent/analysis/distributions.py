"""Deterministic probability policy and Metaculus nonbinary payload adapters."""
import math
from datetime import datetime, timezone

LOWER = 0.02
UPPER = 0.98
POLICY = 'probability-clip-0.02-0.98-v1'
RANGE_TYPES = {'numeric', 'discrete', 'date'}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('A finite number is required')
    return float(value)


def probability(value):
    value = number(value)
    if not 0 <= value <= 1:
        raise ValueError('Probability outside [0, 1]')
    return value


def clip_probability(value):
    return min(UPPER, max(LOWER, probability(value)))


def project_mass(values, lower, upper):
    """Project onto a bounded simplex; clipping alone would break the sum."""
    values = [number(x) for x in values]
    if not values or not len(values) == len(lower) == len(upper):
        raise ValueError('Probability vector bounds do not match')
    if any(not 0 <= lo <= hi <= 1 for lo, hi in zip(lower, upper)):
        raise ValueError('Invalid probability bounds')
    if sum(lower) > 1 + 1e-12 or sum(upper) < 1 - 1e-12:
        raise ValueError('Probability policy is infeasible for this outcome count')
    lo = min(v - hi for v, hi in zip(values, upper)) - 1
    hi = max(v - low for v, low in zip(values, lower)) + 1
    for _ in range(100):
        shift = (lo + hi) / 2
        result = [min(high, max(low, v - shift)) for v, low, high in zip(values, lower, upper)]
        if sum(result) > 1:
            lo = shift
        else:
            hi = shift
    residual = 1 - sum(result)
    for i in range(len(result)):
        change = min(upper[i] - result[i], max(lower[i] - result[i], residual))
        result[i] += change
        residual -= change
    if abs(sum(result) - 1) > 1e-10:
        raise ValueError('Probability projection did not converge')
    return result


def clip_categories(values, options):
    if len(options) < 2 or len(set(options)) != len(options) or set(values) != set(options):
        raise ValueError('Exact current options required')
    raw = [probability(values[k]) for k in options]
    if abs(sum(raw) - 1) > 1e-6:
        raise ValueError('Category probabilities must sum to one')
    clipped = project_mass(raw, [LOWER] * len(raw), [UPPER] * len(raw))
    return dict(zip(options, clipped))


def range_metadata(question):
    kind = question.get('question_type', question.get('type'))
    if kind not in RANGE_TYPES:
        raise ValueError('Numeric, discrete or date metadata required')
    scaling = question.get('scaling') or {}
    minimum, maximum = number(scaling.get('range_min')), number(scaling.get('range_max'))
    if minimum >= maximum:
        raise ValueError('Question range must be increasing')
    count = question.get('inbound_outcome_count')
    if count is None:
        if kind == 'discrete':
            raise ValueError('Discrete outcome count must be explicit')
        count = 200
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError('Unsupported inbound outcome count')
    for name in ('open_lower_bound', 'open_upper_bound'):
        if type(question.get(name)) is not bool:
            raise ValueError('Explicit open-bound flags required')
    zero = scaling.get('zero_point')
    if zero is not None:
        zero = number(zero)
        if kind == 'discrete' or (minimum - zero) * (maximum - zero) <= 0:
            raise ValueError('Invalid logarithmic scaling')
    normalized = {'range_min': minimum, 'range_max': maximum, 'zero_point': zero}
    for key in ('nominal_min', 'nominal_max'):
        if scaling.get(key) is not None:
            normalized[key] = number(scaling[key])
    provided = scaling.get('continuous_range')
    if provided is not None:
        provided = [number(x) for x in provided]
        if len(provided) != count + 1 or any(b <= a for a, b in zip(provided, provided[1:])):
            raise ValueError('Invalid platform CDF grid')
        if not math.isclose(provided[0], minimum) or not math.isclose(provided[-1], maximum):
            raise ValueError('Platform grid does not match range boundaries')
        normalized['continuous_range'] = provided
    return {'type': kind, 'scaling': normalized,
            'inbound_outcome_count': count, 'open_lower_bound': question['open_lower_bound'],
            'open_upper_bound': question['open_upper_bound'], 'unit': question.get('unit', '')}


def nominal(value, meta):
    if meta['type'] == 'date' and isinstance(value, str):
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Date quantiles require explicit timezone')
        return stamp.timestamp()
    return number(value)


def grid(meta):
    meta = range_metadata(meta)
    scale, count = meta['scaling'], meta['inbound_outcome_count']
    low, high, zero = scale['range_min'], scale['range_max'], scale['zero_point']
    if scale.get('continuous_range') is not None:
        return scale['continuous_range']
    # API scaling for discrete questions already expands nominal bounds by
    # half an outcome step. CDF locations are bin edges, not outcome centers.
    if zero is None:
        return [low + (high - low) * i / count for i in range(count + 1)]
    ratio = (high - zero) / (low - zero)
    return [zero + (low - zero) * ratio ** (i / count) for i in range(count + 1)]


def validate_cdf(cdf, question, *, clipped=False):
    meta = range_metadata(question)
    count = meta['inbound_outcome_count']
    values = [probability(x) for x in cdf]
    if len(values) != count + 1:
        raise ValueError('CDF length must be inbound_outcome_count + 1')
    lower = LOWER if clipped else .001
    if meta['open_lower_bound']:
        if values[0] < lower - 1e-9:
            raise ValueError('Missing lower tail probability')
    elif values[0] != 0:
        raise ValueError('Closed lower CDF boundary must equal zero')
    if meta['open_upper_bound']:
        if values[-1] > 1 - lower + 1e-9:
            raise ValueError('Missing upper tail probability')
    elif values[-1] != 1:
        raise ValueError('Closed upper CDF boundary must equal one')
    for a, b in zip(values, values[1:]):
        if not .01 / count - 1e-9 <= b - a <= 40 / count + 1e-9:
            raise ValueError('CDF increment violates platform constraints')
    if clipped and any(not LOWER - 1e-9 <= p <= UPPER + 1e-9 for p in values[1:-1]):
        raise ValueError('Interior CDF violates probability clipping policy')
    return values


def standardize_cdf(cdf, question):
    """Clip cumulative probabilities, then project PMF under platform bounds."""
    meta = range_metadata(question)
    count = meta['inbound_outcome_count']
    values = [probability(x) for x in cdf]
    if len(values) != count + 1 or any(b < a for a, b in zip(values, values[1:])):
        raise ValueError('Raw CDF must have the correct length and be nondecreasing')
    clipped = [clip_probability(x) for x in values]
    if not meta['open_lower_bound']:
        clipped[0] = 0.
    if not meta['open_upper_bound']:
        clipped[-1] = 1.
    mass = [clipped[0]] + [b - a for a, b in zip(clipped, clipped[1:])] + [1 - clipped[-1]]
    # The server rounds CDF entries to 10 decimals and their differences to
    # nine. Keep a small numerical margin inside both increment constraints.
    minimum = round(.01 / count, 9) + 2e-9
    maximum = min(1., 40 / count)
    if maximum < 1:
        maximum = math.floor(maximum * 1e9) / 1e9 - 2e-9
    lower = [LOWER if meta['open_lower_bound'] else 0.] + [minimum] * count + [LOWER if meta['open_upper_bound'] else 0.]
    upper = [UPPER if meta['open_lower_bound'] else 0.] + [maximum] * count + [UPPER if meta['open_upper_bound'] else 0.]
    if count > 1:
        if not meta['open_lower_bound']:
            lower[1] = max(lower[1], LOWER)
        if not meta['open_upper_bound']:
            lower[-2] = max(lower[-2], LOWER)
    projected = project_mass(mass, lower, upper)
    result, cumulative = [], 0.
    for value in projected[:-1]:
        cumulative += value
        result.append(round(cumulative, 10))
    if not meta['open_lower_bound']:
        result[0] = 0.
    if not meta['open_upper_bound']:
        result[-1] = 1.
    validate_cdf(result, meta, clipped=True)
    return result


def interpolate(points, locations):
    if len(points) < 2 or any(b[0] <= a[0] or b[1] < a[1] for a, b in zip(points, points[1:])):
        raise ValueError('Distribution knots must increase in location and probability')
    result = []
    for x in locations:
        if x <= points[0][0]:
            result.append(points[0][1])
            continue
        if x >= points[-1][0]:
            result.append(points[-1][1])
            continue
        for (left, p), (right, q) in zip(points, points[1:]):
            if left <= x <= right:
                result.append(p + (q - p) * (x - left) / (right - left))
                break
    return result


def quantiles_to_cdf(quantiles, question, below, above):
    """Interpolate nominal-unit quantiles; repeated locations represent atoms."""
    meta = range_metadata(question)
    below, above = probability(below), probability(above)
    if below + above >= 1:
        raise ValueError('Tail probabilities leave no inbound mass')
    if not meta['open_lower_bound'] and below != 0 or not meta['open_upper_bound'] and above != 0:
        raise ValueError('Closed bounds cannot have outside probability')
    rows = [(probability(row['probability']), nominal(row['value'], meta)) for row in quantiles]
    if len(rows) < 3 or any(p in (0, 1) for p, _ in rows):
        raise ValueError('At least three interior quantiles required')
    if any(b[0] <= a[0] or b[1] < a[1] for a, b in zip(rows, rows[1:])):
        raise ValueError('Quantile levels must increase and values must not decrease')
    low, high = meta['scaling']['range_min'], meta['scaling']['range_max']
    knots = {low: below, high: 1 - above}
    for p, x in rows:
        if x < low and p > below + 1e-9 or x > high and p < 1 - above - 1e-9:
            raise ValueError('Quantiles contradict outside probability')
        if low <= x < high:
            if p < below - 1e-9 or p > 1 - above + 1e-9:
                raise ValueError('Quantile probability contradicts boundary tails')
            knots[x] = max(knots.get(x, 0), p)
    points = sorted(knots.items())
    locations = grid(meta)
    zero = meta['scaling']['zero_point']
    if zero is not None:
        ratio = (high - zero) / (low - zero)
        def internal(x):
            return math.log((x - zero) / (low - zero)) / math.log(ratio)
        points = [(internal(x), p) for x, p in points]
        locations = [internal(x) for x in locations]
    raw = interpolate(points, locations)
    raw[0], raw[-1] = below, 1 - above
    return raw


def payload(question, forecast):
    ident = question.get('id')
    if isinstance(ident, bool) or not str(ident).isdecimal():
        raise ValueError('Numeric question ID required')
    kind = question.get('question_type', question.get('type'))
    if kind == 'binary':
        result = {'question': int(ident), 'probability_yes': clip_probability(forecast['probability_yes'])}
    elif kind == 'multiple_choice':
        result = {'question': int(ident), 'probability_yes_per_category': clip_categories(forecast['probability_yes_per_category'], question['options'])}
    else:
        result = {'question': int(ident), 'continuous_cdf': standardize_cdf(forecast['continuous_cdf'], question)}
    return result
