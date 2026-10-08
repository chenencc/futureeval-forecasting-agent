"""Independent offline accounting and uncertainty review for the four-task pilot."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from ForecastAgent.market_pulse import financial_chain as c, numeric_binding as binding
from ForecastAgent.market_pulse.financial_recovery import ledger_hashes
from ForecastAgent.analysis.pilot import load, save


def audit(original,recovery,final,output):
    inputs=load(original/'manifest.json')['inputs']
    m=load(final/'manifest.json')
    if ledger_hashes(original)!=m['original_ledger_hashes']:
        raise ValueError('Initial run evidence changed')
    if ledger_hashes(recovery)!=m['prior_recovery']['ledger_hashes']:
        raise ValueError('Recovery evidence changed')
    records=[];physical=[];rows=[]
    for root in (original,recovery,final):
        physical += [load(p) for p in (root/'provider-transport/openrouter/attempts').glob('*.json')]
        records += [(root,p,load(p)) for p in (root/'tasks').glob('*/super/http/*.json')]
        records += [(root,p,load(p)) for p in (root/'tasks').glob('*/repair/http/*.json')]
        records += [(root,p,load(p)) for p in (root/'tasks').glob('*/mercury-*/http/*.json')]
    for item in inputs:
        ident=item['id'];bundle=load(item['source']);folder=final/'tasks'/ident
        if c.base.sha(item['source'])!=item['sha256']:raise ValueError('Original financial package changed')
        result=load(folder/'result.json');analysis=load(folder/'validated-analysis.json')
        # Recompute all accepted equations, not only counters or stage flags.
        derivation={k:analysis[k] for k in ('assumptions','calculations','forecast','thesis','limitations','downside','upside')}
        binding.compile_derivation(derivation,analysis['facts'],c.base.contract(bundle['request']))
        refs=[]
        for fact in analysis['facts']:
            refs.extend([fact['original_row_ref'],fact['unit_original_ref'],*fact['period_original_refs']])
        for ref in refs:
            page=bundle['pages'][ref['url']]
            text=page['content'] if ref['document_index'] is None else page['documents'][ref['document_index']-1]['page_content']
            if ref['quote']!=text[ref['start']:ref['end']]:raise ValueError('Selected financial quote differs from original')
            if ref['text_sha256']!=__import__('hashlib').sha256(text.encode()).hexdigest():raise ValueError('Source text hash differs')
        c.validate_payload(bundle['request'],result['payload'])
        q=result['quantiles'];superq=result['analyst_forecast']
        uncertainty=c.uncertainty_review(bundle['request'],result['payload'],analysis)
        local=[r for _,p,r in records if ident in p.parts]
        super_receipts=[r for r in local if r['request'].get('model')==c.MODEL]
        mercury_receipts=[r for r in local if r['request'].get('model')=='inception/mercury-decide:free']
        limitations=list(analysis['limitations'])
        if ident=='46176':limitations.append('The analyst explicitly uses Q3 FY2025 EPS as a Q4 FY2025 proxy; no same-quarter EPS or current Q4 guidance was supplied.')
        if ident=='46195':limitations.append('An alternative formula uses Q2 revenue as a Q1 seasonal proxy. Valid arithmetic does not validate that quarter mapping or an unsourced 2-percent sequential assumption.')
        if ident=='46193':limitations.append('Management guidance 197-202 billion is not an 80-percent predictive interval; the observed decision interval is substantially narrower.')
        if ident=='46181':limitations.append('Holding Q2 net margin and diluted shares constant is an assumption; the tax guidance is present but the accepted net-margin equation does not separately model future tax or expenses.')
        rows.append({'id':ident,'issuer':result['issuer'],'target_period':result['target_period'],
            'source_file':item['source'],'source_sha256':item['sha256'],
            'raw_execution_status':result['status'],'delivery_status':'requires_financial_and_uncertainty_review',
            'source_numeric_and_quote_binding_passed':True,'arithmetic_replay_passed':True,
            'cdf_format_passed':True,'cdf_points':len(result['payload']['continuous_cdf']),
            'cdf_clip_range':[.02,.98],'semantic_truth_verified':False,
            'financial_facts':analysis['facts'],'assumptions':analysis['assumptions'],
            'calculations':analysis['calculations'],'compatibility_repairs':analysis['compatibility_repairs'],
            'super_quantiles':superq,'mercury_quantiles':q,
            'raw_old_below_probability':next(r['old_below_probability'] for r in load(final/'summary.json')['rows'] if r['id']==ident),
            'raw_new_below_probability':result['raw_forecast']['bin_probabilities']['below'],
            'formatted_below_probability':result['payload']['continuous_cdf'][0],
            'uncertainty_review':uncertainty,'uncertainty_policy_applied_post_hoc':True,
            'limitations':limitations,
            'super_attempts':len(super_receipts),'mercury_attempts':len(mercury_receipts),
            'super_usage':c.base.usage_audit(super_receipts),'mercury_usage':c.base.usage_audit(mercury_receipts),
            'submitted':False,'resolution_known':False,'forecast_accuracy_not_evaluated':True})
    super_records=[r for _,_,r in records if r['request'].get('model')==c.MODEL]
    mercury_records=[r for _,_,r in records if r['request'].get('model')=='inception/mercury-decide:free']
    oldroot=Path(load(original/'manifest.json')['baseline_root'])
    oldrecords=[load(p) for p in (oldroot/'tasks').glob('*/first/http/*.json')]+[load(p) for p in (oldroot/'tasks').glob('*/second/http/*.json')]
    baseline_usage=c.base.usage_audit(oldrecords)
    total=c.base.usage_audit([r for _,_,r in records])
    report={'protocol':'market-pulse-financial-chain-independent-audit-v1',
        'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'pipeline':'Saved original financial reports -> exact numeric fact binding -> Super short derivation -> Mercury typed distribution -> program checks',
        'experiment_roots':[str(r) for r in (original,recovery,final)],
        'questions':rows,'complete_distributions':len(rows),'delivery_ready_questions':0,
        'uncertainty_review_questions':sum(r['uncertainty_review']['requires_review'] for r in rows),
        'actual_physical_provider_attempts':len(physical),
        'physical_http_status_counts':dict(Counter(str(r.get('http_status')) for r in physical)),
        'credential_role_counts':dict(Counter(r.get('credential_role','unknown') for r in physical)),
        'super_model':c.MODEL,'super_journal_attempts':len(super_records),'super_usage':c.base.usage_audit(super_records),
        'mercury_model':'inception/mercury-decide:free','mercury_journal_attempts':len(mercury_records),'mercury_usage':c.base.usage_audit(mercury_records),
        'total_usage':total,'old_baseline_usage':baseline_usage,
        'known_token_change_vs_old_baseline':total['known_tokens']/baseline_usage['known_tokens']-1,
        'models_observed_in_original_requests':sorted({r['request'].get('model','unknown') for _,_,r in records}),
        'quota_and_preservation':{'per_question_super_attempt_cap':3,'per_question_mercury_attempt_cap':2,
            'caps_passed':all(r['super_attempts']<=3 and r['mercury_attempts']<=2 for r in rows),
            'initial_failed_run_unchanged':True,'previous_recovery_unchanged':True,
            'original_packages_unchanged':True,'quote_and_arithmetic_replay_passed':True,
            'quota_reset':False,'new_search_calls':0,'new_acquisition_calls':0,'submitted':False},
        'quality_conclusion':'Numerical traceability and guidance consistency improved in this exploratory same-corpus pilot. All four distributions are much narrower than the analyst uncertainty proposals and need review. No official outcomes or calibration measurements are available.',
        'comparison_warning':'Not a model-only ablation: input financial projection, analyst and decision rubric changed together. Post-hoc uncertainty gate must not be presented as a preregistered accuracy win.'}
    save(output,report)
    print(json.dumps({k:report[k] for k in ('complete_distributions','delivery_ready_questions','uncertainty_review_questions','actual_physical_provider_attempts','super_usage','mercury_usage','total_usage','quota_and_preservation')}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('original','recovery','final','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();audit(a.original,a.recovery,a.final,a.output)
