"""Forecast-error distributions with explicit sample and time availability gates.

Guidance ranges are not confidence intervals. Sparse residuals produce a gap,
never an invented calibrated CDF. This module supplies an empirical candidate;
rolling out-of-sample coverage is still required before calibration is claimed.
"""
from datetime import date
import math
import statistics

VERSION='financial-error-distribution-v1'
MIN_SAMPLES=12


def current_family(derivation,variables):
    """Only recognize an actual guidance-midpoint formula, not a nominal label."""
    central=next((c for c in derivation['calculations'] if c['id']==derivation['central_ref']),{})
    by_id={v['fact_id']:v for v in variables};inputs=central.get('inputs',[])
    if central.get('operation')=='mean' and len(inputs)==2 and all(i in by_id for i in inputs):
        selected=[by_id[i] for i in inputs]
        if all(v['metric']=='revenue' and v['role']=='management_guidance' and v['normalized_unit']=='USD' for v in selected):
            periods={v.get('period_interpretation',v.get('period_claim')) for v in selected}
            if len(periods)==1:return 'management_guidance_midpoint'
    return 'unsupported_current_formula'


def audit_pairs(pairs,as_of,metric,unit,model_family):
    kept=[];excluded=[];periods=set()
    cutoff=date.fromisoformat(as_of)
    for row in pairs:
        reason=None
        try:
            issued=date.fromisoformat(row['issued_at']);known=date.fromisoformat(row['actual_published_at'])
            if not issued<known<cutoff:reason='not_available_at_forecast_origin'
            if row['metric']!=metric or row['unit']!=unit or row['model_family']!=model_family:reason='incompatible_error_family'
            if not row.get('first_report_verified'):reason='first_release_unverified'
            if not all(type(row[k]) in (int,float) and math.isfinite(row[k]) for k in ('forecast','actual')) or row['forecast']<=0:reason='invalid_numeric_pair'
            if not row.get('forecast_ref') or not row.get('actual_ref'):reason='unbound_pair'
            if row['period'] in periods:reason='duplicate_target_period'
        except (KeyError,ValueError,TypeError):reason='invalid_pair_metadata'
        if reason:excluded.append({'period':row.get('period'),'reason':reason});continue
        periods.add(row['period']);kept.append({**row,'relative_error':row['actual']/row['forecast']-1})
    return kept,excluded


def fit(pairs,as_of,metric,unit,model_family,central,question):
    rows,excluded=audit_pairs(pairs,as_of,metric,unit,model_family)
    result={'protocol':VERSION,'sample_count':len(rows),'minimum_samples':MIN_SAMPLES,
        'residuals':rows,'excluded':excluded,'calibration_validated':False,
        'forecast_accuracy_not_evaluated':True,'model_family':model_family,'cdf':None}
    if len(rows)<MIN_SAMPLES:
        result.update(status='insufficient_error_history',gap='Collect more first-release, time-bound forecast/actual pairs; no probability interval inferred from guidance.')
        return result
    errors=[r['relative_error'] for r in rows];spread=statistics.stdev(errors)
    if not spread>0 or not math.isfinite(central) or central<=0:
        result.update(status='degenerate_error_history',gap='A zero-spread or invalid center cannot establish future uncertainty.');return result
    bandwidth=1.06*spread*len(errors)**(-.2)
    from ForecastAgent.analysis.distributions import grid,range_metadata
    locations=grid(range_metadata(question));normal=statistics.NormalDist()
    def at(value):return sum(normal.cdf((value/central-1-e)/bandwidth) for e in errors)/len(errors)
    result.update(status='empirical_candidate_requires_rolling_validation',bandwidth_relative=bandwidth,
        raw_cdf=[at(x) for x in locations],distribution_family='Gaussian kernel mixture of relative forecast errors',
        assumptions=['Historical error process remains relevant to current regime.','Quarter errors are treated as exchangeable; dependence and structural change are not resolved.'])
    result['cdf']=result['raw_cdf']
    return result


def interval_score(actual,low,high,alpha=.2):
    if not 0<alpha<1 or high<low:raise ValueError('Invalid interval')
    return high-low+(2/alpha)*(low-actual if actual<low else actual-high if actual>high else 0)
