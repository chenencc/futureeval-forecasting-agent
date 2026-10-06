"""Shared question metadata and scoring definitions for the fixed typed cohort."""
import copy
import math
from datetime import datetime

from ForecastAgent.acquisition.pipeline import reject_outcomes
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import grid, validate_cdf, probability
from ForecastAgent.competition.mercury import distribution_spec


def request(original, row):
    """Fill explicit archived scale fields without changing original wording."""
    if original['question'] != row['title'] or original['resolution_criteria'] != row['criteria']:
        raise ValueError('Saved question wording differs from the frozen core input')
    result = copy.deepcopy(original)
    shared = typed.request(row)
    for name, value in shared.items():
        if name == 'background':
            continue
        if name == 'scaling' and value is not None and result.get(name) is not None:
            scale = copy.deepcopy(result[name])
            for field, supplied in value.items():
                if field in scale and scale[field] != supplied:
                    raise ValueError('Archived scaling metadata disagrees: ' + field)
                scale[field] = copy.deepcopy(supplied)
            result[name] = scale
            continue
        if name in result and result[name] is not None and value is not None and result[name] != value:
            raise ValueError('Archived question metadata disagrees: ' + name)
        if value is not None or name not in result:
            result[name] = value
    reject_outcomes(result)
    return result


def spec(question, row):
    if row['type'] == 'date':
        return typed.spec(row)
    result = distribution_spec(question)
    result.setdefault('official_metadata_available', True)
    return result


def bounded_crps(knots, value):
    """Exact integral of piecewise linear CDF loss within supplied bounds."""
    score = 0.0
    for (a, p), (b, q) in zip(knots, knots[1:]):
        def integral(left, right, first, last, observed):
            x, y = first-observed, last-observed
            return (right-left)*(x*x+x*y+y*y)/3
        if value <= a:
            score += integral(a, b, p, q, 1)
        elif value >= b:
            score += integral(a, b, p, q, 0)
        else:
            middle = p+(q-p)*(value-a)/(b-a)
            score += integral(a, value, p, middle, 0)+integral(value, b, middle, q, 1)
    return score


def score(forecast, distribution_spec, label):
    """Engineering metrics only; never substitute for Metaculus Peer scores."""
    kind = distribution_spec['kind']
    if kind == 'multiple_choice':
        options = distribution_spec['options']
        index = label['option_index_zero_based']
        if type(index) is not int or not 0 <= index < len(options) or options[index] != label['resolution_display']:
            raise ValueError('Evaluation option identity mismatch')
        probabilities = forecast['clipped_probabilities']
        if set(probabilities) != set(options) or abs(sum(probabilities.values())-1) > 1e-8:
            raise ValueError('Exact normalized category probabilities required')
        if any(not .02-1e-9 <= probability(p) <= .98+1e-9 for p in probabilities.values()):
            raise ValueError('Category clipping policy violated')
        truth = options[index]
        return {'multiclass_brier': sum((p-(option==truth))**2 for option,p in probabilities.items()),
                'log_loss': -math.log(probabilities[truth]),
                'top_option_correct': max(probabilities, key=probabilities.get)==truth,
                'probability_of_true_option': probabilities[truth], 'payload_format_valid': True}
    meta = distribution_spec['meta']
    knots = (list(zip(grid(meta), validate_cdf(forecast['continuous_cdf'], meta, clipped=True)))
             if meta is not None else forecast['cdf_knots'])
    if len(knots) < 2 or any(b[0] <= a[0] or b[1] < a[1] for a,b in zip(knots,knots[1:])):
        raise ValueError('Increasing grid and monotonic CDF required')
    for x,p in knots:
        if not math.isfinite(x):
            raise ValueError('Finite grid coordinates required')
        probability(p)
    outcome = label['result_kind']
    value = (-math.inf if outcome == 'below_lower_bound' else math.inf if outcome == 'above_upper_bound'
             else datetime.fromisoformat(label['value'].replace('Z','+00:00')).timestamp() if kind == 'date'
             else label['value'])
    if outcome not in ('below_lower_bound', 'above_upper_bound') and not math.isfinite(value):
        raise ValueError('Finite resolving values required outside censored tails')
    low, high = knots[0][0], knots[-1][0]
    loss = bounded_crps(knots, value)
    result = {'bounded_normalized_crps': loss/(high-low),
              'payload_format_valid': bool(forecast['payload_format_valid']),
              'official_metadata_available': distribution_spec['official_metadata_available']}
    if kind == 'date':
        result['bounded_crps_days'] = loss/86400
    if outcome in ('below_lower_bound','above_upper_bound'):
        mass = knots[0][1] if outcome=='below_lower_bound' else 1-knots[-1][1]
        result.update(tail_probability=mass, tail_event_brier=(1-mass)**2,
                      exact_tail_value_unknown=True)
    return result
