"""Explicit program anchors and unweighted sensitivities over canonical facts.

These are named baseline calculations, never replacement model forecasts or
empirically calibrated probability intervals. Source-role selection is explicit.
"""
import math


def fact(variables,ident,metric,kind):
    row=next(v for v in variables if v['fact_id']==ident)
    if row['metric']!=metric or row['normalized_unit']!=kind:raise ValueError('Projection input metric or dimensions differ')
    value=row['normalized_value']
    if not math.isfinite(value):raise ValueError('Projection input is not finite')
    return value


def revenue_anchor(variables,lower,upper):
    low=fact(variables,lower,'revenue','USD');high=fact(variables,upper,'revenue','USD')
    selected=[next(v for v in variables if v['fact_id']==i) for i in (lower,upper)]
    if low>=high or any(v['role']!='management_guidance' for v in selected):raise ValueError('Revenue anchor needs ordered management guidance')
    return {'name':'management_guidance_midpoint','source_refs':[lower,upper],'value':(low+high)/2,
        'unit':'USD','guidance_bounds':[low,high],'guidance_is_not_probability_interval':True,
        'formula':'(lower_guidance + upper_guidance) / 2','forecast_accuracy_validated':False}


def eps_sensitivity(variables,refs):
    anchor=revenue_anchor(variables,refs['guidance_lower'],refs['guidance_upper'])
    revenue=fact(variables,refs['prior_revenue'],'revenue','USD')
    income=fact(variables,refs['prior_net_income'],'net_income','USD')
    shares=fact(variables,refs['prior_diluted_shares'],'diluted_shares','shares')
    tax_low=fact(variables,refs['tax_lower'],'tax_rate','fraction');tax_high=fact(variables,refs['tax_upper'],'tax_rate','fraction')
    costs=sum(fact(variables,i,'one_off','USD') for i in refs['current_expense_one_offs'])
    if revenue<=0 or shares<=0 or not 0<=tax_low<=tax_high<1:raise ValueError('Invalid EPS predictor values')
    margin=income/revenue;tax=(tax_low+tax_high)/2;scaling=anchor['value']/revenue
    baseline=anchor['value']*margin/shares
    scenarios=[{'removed_fraction':p,'eps':baseline+costs*(1-tax)*p*scaling/shares,'probability':None} for p in (0,.5,1)]
    return {'name':'prior_net_margin_with_explicit_cost_recurrence_sensitivity','source_refs':refs,
        'revenue_anchor':anchor,'prior_net_margin':margin,'prior_diluted_shares':shares,
        'baseline_eps':baseline,'assumed_tax_for_removed_cost':tax,'current_expense_one_offs':costs,
        'scenarios':scenarios,'unit':'USD_per_share','probability_interval':None,
        'assumptions':['Prior net margin and diluted shares persist in the target quarter.',
            'Cost removal scenarios are unweighted and may not recur as assumed.',
            'Tax applies only to hypothetical removed pretax cost; existing net income is not taxed twice.',
            'Costs are scaled with forecast revenue to match the prior-margin extrapolation; this is a sensitivity convention, not observed truth.'],
        'formula':'target_revenue * prior_net_income / prior_revenue / prior_shares + removed_fraction * current_one_off_costs * (1-tax) * target_revenue/prior_revenue/prior_shares',
        'semantic_truth_verified':False,'calibration_validated':False}
