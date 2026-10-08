"""Typed financial templates; the program owns rates, units and equations."""
import math
import re

from ForecastAgent.market_pulse.financial_chain import finite, obj


def fiscal_period(text):
    text = str(text)
    match = re.search(r'Q([1-4])\s*FY\s*(20\d{2})', text, re.I)
    if match:
        return int(match[1]), int(match[2])
    match = re.search(r'fiscal\s+(20\d{2})\s+(first|second|third|fourth)\s+quarter', text, re.I)
    if match:
        return ('first', 'second', 'third', 'fourth').index(match[2].lower()) + 1, int(match[1])
    return None


def period(v):
    return fiscal_period(v.get('period_interpretation') or v.get('period_claim'))


def templates(variables, allowed, financial):
    vs = [v for v in variables if v['fact_id'] in allowed]
    mapping = {v['fact_id']: v for v in vs}
    guides = [v for v in vs if v['metric'] == 'revenue' and v['role'] == 'management_guidance']
    result = []
    guide_periods = {v.get('period_interpretation') or v.get('period_claim') for v in guides}
    if len(guides) == 2 and len(guide_periods) == 1 and None not in guide_periods and all(v['normalized_unit'] == 'USD' for v in guides):
        guides.sort(key=lambda v: v['normalized_value'])
        refs = {'guidance_lower': guides[0]['fact_id'], 'guidance_upper': guides[1]['fact_id']}
        if financial['metric'] == 'quarterly_revenue':
            result.append({'method': 'guidance_midpoint', 'refs': refs,
                'growth_rate_meaning': 'Must be zero. Guidance already includes the stated growth outlook; midpoint baseline is fixed.'})
        else:
            for ni in vs:
                if ni['metric'] != 'net_income' or ni['role'] != 'actual' or ni['basis'] != 'GAAP' or not period(ni):
                    continue
                revenues = [v for v in vs if v['metric'] == 'revenue' and v['role'] == 'actual' and v['basis'] == 'GAAP' and period(v) == period(ni)]
                shares = [v for v in vs if v['metric'] == 'diluted_shares' and v['role'] == 'actual' and v['basis'] == 'GAAP' and period(v) == period(ni)]
                if len(revenues) != 1 or len(shares) != 1:
                    continue
                costs = [v['fact_id'] for v in vs if v['metric'] == 'one_off' and period(v) == period(ni)
                    and re.search(r'legal|severance|pretax|pre-tax', v['original_row_ref']['quote'], re.I)
                    and not re.search(r'income tax charge', v['original_row_ref']['quote'], re.I)]
                taxes = [v for v in vs if v['metric'] == 'tax_rate' and v['role'] == 'management_guidance']
                taxes.sort(key=lambda v: v['normalized_value'])
                source_refs = {**refs, 'net_income': ni['fact_id'], 'prior_revenue': revenues[0]['fact_id'],
                    'shares': shares[0]['fact_id'], 'pretax_costs': costs}
                if len(taxes) == 2:
                    source_refs.update(tax_lower=taxes[0]['fact_id'], tax_upper=taxes[1]['fact_id'])
                result.append({'method': 'net_margin_projection', 'refs': source_refs,
                    'predictor_period': period(ni),
                    'growth_rate_meaning': 'Assumed relative change in PRIOR NET MARGIN, not a revenue growth rate.',
                    'cost_removed_fraction_meaning': 'Fraction of identified prior pretax costs that will not recur; not an observed future fact.'})
    if financial['metric'] == 'gaap_diluted_eps':
        target = fiscal_period(financial['target_period'])
        for v in vs:
            if (target and v['metric'] == 'diluted_eps' and v['basis'] == 'GAAP' and v['role'] == 'actual'
                    and period(v) == (target[0], target[1] - 1)):
                result.append({'method': 'comparable_quarter_eps_growth', 'refs': {'prior_eps': v['fact_id']},
                    'predictor_period': period(v),
                    'growth_rate_meaning': 'Assumed year-over-year EPS growth from the SAME fiscal quarter of the prior year.'})
    for i, template in enumerate(result, 1):
        template['template_id'] = 'T' + str(i)
        ids = set(value for value in template['refs'].values() if isinstance(value, str))
        ids.update(value for values in template['refs'].values() if isinstance(values, list) for value in values)
        template['canonical_inputs'] = {i: {'value': mapping[i]['normalized_value'], 'unit': mapping[i]['normalized_unit']} for i in sorted(ids)}
    return result


def tool(candidates):
    return {'type': 'function', 'function': {'name': 'record_template_assumptions',
        'description': 'Choose an existing source-bound template and declare fractional future assumptions. No numbers or formulas are recomputed by the model.',
        'parameters': obj({'template_id': {'type': 'string', 'enum': [t['template_id'] for t in candidates]},
            'growth_rate': {'type': 'number', 'minimum': -.9, 'maximum': 1},
            'cost_removed_fraction': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'share_change_rate': {'type': 'number', 'minimum': -.5, 'maximum': .5},
            'growth_reason': {'type': 'string'}, 'cost_reason': {'type': 'string'}, 'share_reason': {'type': 'string'},
            'limitations': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 5}})}}


def evaluate(choice, candidates):
    if set(choice) != set(tool(candidates)['function']['parameters']['properties']):
        raise ValueError('Template assumption schema differs')
    options = [t for t in candidates if t['template_id'] == choice['template_id']]
    if len(options) != 1:
        raise ValueError('Unknown financial template')
    t = options[0]; values = t['canonical_inputs']; refs = t['refs']
    g = finite(choice['growth_rate']); removal = finite(choice['cost_removed_fraction']); shares_change = finite(choice['share_change_rate'])
    if not -.9 <= g <= 1 or not 0 <= removal <= 1 or not -.5 <= shares_change <= .5:
        raise ValueError('Fractional assumption outside declared range')
    for name in ('growth_reason', 'cost_reason', 'share_reason'):
        if not isinstance(choice[name], str) or not choice[name].strip() or len(choice[name]) > 1200:
            raise ValueError('Explicit bounded assumption reason required')
    def v(role, unit):
        value = values[refs[role]]
        if value['unit'] != unit:
            raise ValueError('Template source dimensions differ')
        return finite(value['value'])
    if t['method'] == 'guidance_midpoint':
        if g or removal or shares_change:
            raise ValueError('Guidance already includes growth; the guidance-midpoint baseline accepts no additional growth, cost or share adjustment')
        anchor = (v('guidance_lower', 'USD') + v('guidance_upper', 'USD')) / 2
        center = anchor * (1 + g); unit = 'USD'
        equation = '(guidance_lower + guidance_upper) / 2 * (1 + growth_rate)'
        sensitivities = [{'growth_rate': rate, 'value': anchor * (1 + rate), 'probability': None} for rate in sorted({g - .02, g, g + .02})]
    elif t['method'] == 'comparable_quarter_eps_growth':
        if removal or shares_change:
            raise ValueError('Comparable EPS growth already includes shares; separate adjustments forbidden')
        anchor = v('prior_eps', 'USD_per_share'); center = anchor * (1 + g); unit = 'USD_per_share'
        equation = 'same_fiscal_quarter_prior_year_GAAP_EPS * (1 + growth_rate)'
        sensitivities = [{'growth_rate': rate, 'value': anchor * (1 + rate), 'probability': None} for rate in sorted({g - .05, g, g + .05})]
    else:
        revenue = (v('guidance_lower', 'USD') + v('guidance_upper', 'USD')) / 2
        prior_revenue = v('prior_revenue', 'USD'); shares = v('shares', 'shares') * (1 + shares_change)
        if prior_revenue <= 0 or shares <= 0:
            raise ValueError('Template denominator nonpositive')
        net_margin = v('net_income', 'USD') / prior_revenue
        costs = sum(finite(values[i]['value']) for i in refs['pretax_costs'])
        if any(values[i]['unit'] != 'USD' for i in refs['pretax_costs']):
            raise ValueError('Pretax cost dimensions differ')
        if removal and (not costs or 'tax_lower' not in refs):
            raise ValueError('Cost removal requires original pretax costs and a tax-rate source')
        tax = (v('tax_lower', 'fraction') + v('tax_upper', 'fraction')) / 2 if 'tax_lower' in refs else 0
        def eps(fraction):
            return (revenue * net_margin * (1 + g) + fraction * costs * (1 - tax) * revenue / prior_revenue) / shares
        center = eps(removal); unit = 'USD_per_share'; anchor = eps(0)
        equation = '(target_revenue * prior_net_margin * (1 + margin_growth) + removed_pretax_costs * (1-tax) * target_revenue/prior_revenue) / projected_shares'
        sensitivities = [{'cost_removed_fraction': fraction, 'value': eps(fraction), 'probability': None} for fraction in (0, .5, 1)] if costs and 'tax_lower' in refs else []
    if not math.isfinite(center):
        raise ValueError('Template result nonfinite')
    return {'template': t, 'assumptions': choice, 'central_value': center, 'unit': unit,
        'program_equation': equation, 'baseline_anchor': anchor, 'sensitivities': sensitivities,
        'arithmetic_and_units_valid': True, 'same_quarter_rule_enforced': t['method'] == 'comparable_quarter_eps_growth',
        'semantic_truth_verified': False, 'calibration_validated': False,
        'sensitivity_values_are_not_probability_intervals': True}
