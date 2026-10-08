"""Quote-bound guidance targets and units, independent of issuer or forecasts.

This adapter does not certify ambiguous dates or remove delivery holds.
Administrative annulment conditions remain distinct from numeric targets.
"""
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import re

PERIOD = r'Q([1-4])\s+FY\s*(\d{4})'
LABELS = {
    'revenue': ('revenue_guidance', 'USD_billions', Decimal('1')),
    'gross margin (gaap)': ('gross_margin_guidance', 'percentage_points', Decimal('0.1')),
    'operating expenses (gaap)': ('operating_expense_guidance', 'USD_billions', Decimal('0.1')),
}


def reference(request, field, start, end):
    text = request[field]
    return {'field': field, 'start': start, 'end': end, 'quote': text[start:end],
        'field_sha256': hashlib.sha256(text.encode()).hexdigest()}


def periods(text):
    return [{'quarter': int(m[1]), 'fiscal_year': int(m[2]),
        'start': m.start(), 'end': m.end()} for m in re.finditer(PERIOD, text, re.I)]


def period_name(value):
    return f"Q{value['quarter']} FY{value['fiscal_year']}"


def next_period(value):
    q, year = value['quarter'], value['fiscal_year']
    return {'quarter': q % 4 + 1, 'fiscal_year': year + (q == 4)}


def contract(request):
    title, criteria, fine = [request.get(k, '') for k in
        ('question', 'resolution_criteria', 'fine_print')]
    # Longest label first prevents a parenthetical accounting basis from being
    # mistaken for the actual leaf label. Non-GAAP never aliases to GAAP.
    match = next((key for key in sorted(LABELS, key=len, reverse=True)
        if title.casefold().endswith('(' + key + ')')), None)
    if not match or 'guidance' not in title.casefold():
        raise ValueError('Supported exact guidance leaf label required')
    metric, normalized_unit, step = LABELS[match]
    expected = '%' if normalized_unit == 'percentage_points' else 'Billion $'
    if request.get('unit') != expected:
        raise ValueError('Guidance question unit conflicts with metric definition')
    target_match = re.search(r'midpoint of the guidance range for\s+' + PERIOD,
        criteria, re.I)
    release_match = re.search(r'in\s+[^.\n]*?\b' + PERIOD + r'\s+earnings press release',
        criteria, re.I)
    if not target_match or not release_match:
        raise ValueError('Target guidance and publishing release need explicit rule quotes')
    target = {'quarter': int(target_match[1]), 'fiscal_year': int(target_match[2])}
    release = {'quarter': int(release_match[1]), 'fiscal_year': int(release_match[2])}
    title_periods = periods(title)
    if len(title_periods) != 1 or any(title_periods[0][k] != release[k] for k in ('quarter', 'fiscal_year')):
        raise ValueError('Title and numeric rule disagree on publishing release')
    if next_period(release) != target:
        raise ValueError('Forward quarter is not the quarter after the publishing release')
    conditions = []
    for m in re.finditer(r'^\s*[*-]\s+If[^\n]*\bannulled\b[^\n]*', fine, re.M | re.I):
        claims = periods(m.group())
        conditions.append({'role': 'administrative_annulment_condition',
            'condition_ref': reference(request, 'fine_print', m.start(), m.end()),
            'fiscal_periods': [period_name(p) for p in claims],
            'status': 'unverified', 'changes_numeric_target': False,
            'interpretation_requires_review': len(claims) != 2})
    rounding = ('nearest billion' if metric == 'revenue_guidance' else
        'nearest tenth of a percent' if metric == 'gross_margin_guidance' else
        'nearest tenth of a billion')
    if rounding not in criteria.casefold():
        raise ValueError('Original rule does not state the expected rounding convention')
    return {'schema': 'financial-guidance-contract-v1', 'metric': metric,
        'publishing_release_period': period_name(release),
        'target_guidance_period': period_name(target), 'normalized_unit': normalized_unit,
        'unit_to_raw_usd': 1_000_000_000 if normalized_unit == 'USD_billions' else None,
        'rounding_step_in_question_units': str(step),
        'basis': 'GAAP' if 'gaap' in match else 'not_applicable',
        'target_ref': reference(request, 'resolution_criteria', target_match.start(), target_match.end()),
        'release_ref': reference(request, 'resolution_criteria', release_match.start(), release_match.end()),
        'administrative_conditions': conditions,
        'settlement_policy': 'Initially published primary guidance midpoint, or single figure; apply original rounding.',
        'previous_guidance_role': 'Predictor or prerequisite evidence only; never the future resolving figure.',
        'expected_publication_date_status': 'requires_separate_review',
        'delivery_authorized_by_adapter': False,
        'original_fields_sha256': {k: hashlib.sha256(request.get(k, '').encode()).hexdigest()
            for k in ('question', 'resolution_criteria', 'fine_print', 'background')}}


def convert(value, source_unit, target_unit):
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError('Nonfinite financial quantity')
    currency = {'USD': Decimal(1), 'USD_millions': Decimal('1e6'), 'USD_billions': Decimal('1e9')}
    ratios = {'fraction': Decimal(1), 'percentage_points': Decimal('0.01')}
    family = currency if source_unit in currency and target_unit in currency else ratios
    if source_unit not in family or target_unit not in family:
        raise ValueError('Incompatible financial unit dimensions')
    return value * family[source_unit] / family[target_unit]


def resolving_value(lower, upper, *, source_unit, target_unit, step):
    """Model the rule transform; exact halfway ties still require review."""
    a = convert(lower, source_unit, target_unit)
    b = a if upper is None else convert(upper, source_unit, target_unit)
    if b < a:
        raise ValueError('Reversed guidance range')
    size = Decimal(str(step))
    if not size.is_finite() or size <= 0:
        raise ValueError('Invalid rounding step')
    value = (a+b)/2
    fraction = value/size - (value/size).to_integral_value(rounding='ROUND_FLOOR')
    return {'midpoint': str(value), 'rounded': str((value/size).to_integral_value(rounding=ROUND_HALF_UP)*size),
        'exact_halfway_tie_requires_review': fraction == Decimal('.5'),
        'target_unit': target_unit, 'is_forecast': False}
