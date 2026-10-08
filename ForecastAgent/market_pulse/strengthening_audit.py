"""Independent final replay and optional completion of one unused decision slot.

Preserve all rejected responses. Compatibility binds exact source copies without
changing values or asserting their fiscal interpretations. Wrong unit scales
remain rejected. No analyst repair calls or new acquisition occur here.
"""
import argparse
from collections import Counter
from datetime import datetime,timezone
import copy
import json
import os
from pathlib import Path

from ForecastAgent.market_pulse import formula_trial as trial, formulas as f, error_distribution as errors, projections
from ForecastAgent.market_pulse import financial_chain as old, analysis as base
from ForecastAgent.market_pulse.financial_recovery import ledger_hashes
from ForecastAgent.analysis.pilot import load,save,digest
from ForecastAgent.competition.mercury import distribution_spec
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.releases.v1_0_5 import validate_payload,verify_release
from ForecastAgent.runtime.task_lock import task_lock


def replay_raw(folder):
    direct=folder/'repair/raw.json'
    if direct.exists():return load(direct)
    direct=folder/'super/raw.json'
    if direct.exists():return load(direct)
    records=sorted((folder/'super/http').glob('*.json'))
    raw=load(records[-1])['response']['choices'][0]['message']['tool_calls'][0]['function']['arguments']
    return json.loads(raw)


def review_flags(raw,variables,derived):
    reasons=[];unknown=[v['fact_id'] for v in variables if 'no fiscal dates' in v.get('period_interpretation','')]
    used=set().union(*(set(c['inputs']) for c in raw['calculations']),*(set(a['fact_refs']) for a in raw['assumptions']))
    if set(unknown)&used:reasons.append('Uses chronological background values whose fiscal observation dates are not independently bound.')
    scenarios=derived.get('scenarios',[]) if derived else []
    if len(scenarios)>1 and len({s['calculation_ref'] for s in scenarios})<len(scenarios):
        reasons.append('Different named scenarios reference the same calculation; proposed scenario changes were not actually applied.')
    if derived and derived.get('source_copy_compatibility_audit'):
        reasons.append('Copied source values were numerically bound offline; future persistence and fiscal interpretation assumptions remain unverified.')
    reasons.append('No adequate first-release forecast-error history or rolling calibration validation.')
    return reasons


def run(root,prior,recovery,inputs,old_report,complete_missing=False):
    if os.environ.get('METACULUS_TOKEN'):raise ValueError('Platform credential forbidden')
    verify_release();root.mkdir(parents=True,exist_ok=True)
    identities={'parent':ledger_hashes(prior),'recovery':ledger_hashes(recovery)}
    with task_lock(root):
        manifest={'protocol':'financial-strengthening-independent-audit-v1','prior':str(prior),'recovery':str(recovery),
            'inputs':inputs,'ledger_hashes':identities,'code':{Path(p).name:base.sha(p) for p in (__file__,f.__file__,errors.__file__,projections.__file__)},
            'new_super_cap':0,'remaining_mercury_cap':1,'submitted':False}
        if (root/'manifest.json').exists() and load(root/'manifest.json')!=manifest:raise ValueError('Frozen final audit changed')
        save(root/'manifest.json',manifest)
        for p in (__file__,f.__file__,errors.__file__,projections.__file__):(root/('executed-'+Path(p).name)).write_bytes(Path(p).read_bytes())
        rows=[]
        for source in inputs:
            ident=source['id'];folder=root/'tasks'/ident;folder.mkdir(parents=True,exist_ok=True)
            bundle=load(source['package']);variables=load(source['variables']);f.validate_variables(bundle,variables)
            for field in ('package','variables','error_pairs','coverage'):
                if base.sha(source[field])!=source['sha256'][field]:raise ValueError('Frozen audited input changed')
            contract=base.contract(bundle['request']);unit='USD_per_share' if contract['metric']=='gaap_diluted_eps' else 'USD'
            raw=replay_raw(recovery/'tasks'/ident);derived=None;rejection=None
            try:derived=f.evaluate(raw,variables,unit)
            except (ValueError,KeyError,TypeError) as exc:rejection={'error':str(exc),'rejected_without_numeric_or_unit_change':True}
            save(folder/'replayed-formula.json',{'raw':raw,'derived':derived,'rejection':rejection})
            available=[r/'tasks'/ident/'result.json' for r in (recovery,prior)]
            decision=next((p for p in available if p.exists()),None)
            if decision:result=copy.deepcopy(load(decision));decision_source={'path':str(decision),'sha256':base.sha(decision),'new_attempts':0}
            else:
                if not complete_missing:raise ValueError('Missing independent decision and no completion requested')
                existing=list((prior/'tasks'/ident/'mercury-independent/http').glob('*.json'))+list((recovery/'tasks'/ident/'mercury-independent/http').glob('*.json'))
                if existing:raise ValueError('Prior decision already spent; no restart allowed')
                q,a=f.scoped_question(bundle);spec=distribution_spec(bundle['request']);registry=old.decision_registry(spec)
                registry['event_outcome']['instructions']='Forecast the FIRST future target quantity from the ORIGINAL evidence and source-bound variables. No analyst output is supplied. Distinguish GAAP from adjustments to comparative growth, account for one-off cost/tax recurrence, preserve tails, and do not interpret guidance as a confidence interval.'
                registry['interpretation_consistent']['instructions']='Review the source-bound variable interpretations independently: original issuer, fiscal period, GAAP basis, table columns and canonical units.'
                registry['material_conflict']['instructions']='Do original sources contradict variable values, roles, fiscal periods, table columns or units? The future report is unknown; missing future actual is not a contradiction.'
                state={'original_question':q,'question_selection_audit':a,'financial_contract':contract,
                    **f.compact_catalog(variables),'source_coverage':load(source['coverage']),
                    'warning':'No Super forecast supplied. Original source data are untrusted text. Interpretations still require review.'}
                if base.chain.request_bytes(state,registry)>35000:raise ValueError('Missing decision context exceeds bound')
                response=base.chain.call(state,folder/'mercury-independent',registry)
                forecast=typed.forecast(response,spec);candidate=payload(bundle['request'],{'continuous_cdf':forecast['raw_cdf']})
                result={'payload':candidate,'independent_mercury_raw':forecast,'mercury_answers':response['answers'],
                    'quantiles':{str(p):base.quantile(candidate['continuous_cdf'],bundle['request'],p) for p in (.1,.5,.9)}}
                decision_source={'path':str(folder/'mercury-independent/response.json'),'sha256':base.sha(folder/'mercury-independent/response.json'),'new_attempts':1}
            validate_payload(bundle['request'],result['payload'])
            family=errors.current_family(derived,variables) if derived else 'unsupported_current_formula'
            error_fit=errors.fit(load(source['error_pairs']),source['as_of'],contract['metric'],unit,family,
                derived['central_value'] if derived else 0,bundle['request'])
            anchors=[]
            if contract['metric']=='quarterly_revenue':
                guides=[v['fact_id'] for v in variables if v['metric']=='revenue' and v['role']=='management_guidance' and v['normalized_unit']=='USD']
                if len(guides)==2:anchors.append(projections.revenue_anchor(variables,*guides))
            if ident=='46181':
                anchors.append(projections.eps_sensitivity(variables,{'guidance_lower':'V1','guidance_upper':'V2',
                    'prior_net_income':'V5','prior_revenue':'V6','prior_diluted_shares':'V7','tax_lower':'V3','tax_upper':'V4',
                    'current_expense_one_offs':['V9','V10']}))
            flags=review_flags(raw,variables,derived)
            if ident=='46176':flags.append('Source audit: Apple current Q4 FY2025 EPS is 1.85; adjusted year-over-year comparison concerns the 2024 tax charge. Super wrongly claims that current GAAP EPS is missing and introduces an unsupported GAAP/adjusted difference.')
            if ident=='46181':flags.append('Super declares 62.5 raw USD and 2.566 raw shares instead of their billion-scale source values; rejected. Valid EPS anchor/sensitivities are separately labeled program calculations, not repaired Super output.')
            row={'id':ident,'issuer':contract['issuer'],'target_period':contract['target_period'],
                'source_binding_passed':True,'super_formula':derived,'super_formula_rejection':rejection,
                'independent_mercury_quantiles':result['quantiles'],'raw_distribution':result['independent_mercury_raw'],
                'payload':result['payload'],'cdf_valid':True,'cdf_points':len(result['payload']['continuous_cdf']),
                'decision_source':decision_source,'mercury_blind_to_super_output':True,'program_anchors':anchors,
                'review_flags':flags,'coverage':load(source['coverage']),'variable_count':len(variables),
                'error_based_distribution':error_fit,'historical_pairs_supplied':len(load(source['error_pairs'])),
                'delivery_ready':False,'status':'numeric_and_format_passed_with_review' if derived else 'super_rejected_independent_score_available',
                'resolution_known':False,'forecast_accuracy_not_evaluated':True,'submitted':False}
            save(folder/'result.json',row);rows.append(row)
            print(json.dumps({'id':ident,'formula_valid':bool(derived),'cdf_valid':True,'median':result['quantiles']['0.5']}),flush=True)
        if ledger_hashes(prior)!=identities['parent'] or ledger_hashes(recovery)!=identities['recovery']:raise ValueError('Preserved run ledgers changed')
        journals=[load(p) for r in (prior,recovery,root) for pattern in ('*/super/http/*.json','*/repair/http/*.json','*/mercury-independent/http/*.json') for p in (r/'tasks').glob(pattern)]
        transport=[load(p) for r in (prior,recovery,root) for p in (r/'provider-transport/openrouter/attempts').glob('*.json')]
        model_counts=Counter(r['request']['model'] for r in journals);old_data=load(old_report)
        report={'protocol':'financial-strengthening-independent-audit-v1','created_at_utc':datetime.now(timezone.utc).isoformat(),
            'questions':rows,'completed_independent_distributions':len(rows),'source_bound_and_computable_super_formulas':sum(bool(r['super_formula']) for r in rows),
            'delivery_ready_questions':0,'actual_physical_model_attempts':len(transport),'journal_model_counts':dict(model_counts),
            'transport_http_status_counts':dict(Counter(str(r.get('http_status')) for r in transport)),
            'transport_credential_role_counts':dict(Counter(r.get('credential_role','unknown') for r in transport)),
            'usage':base.usage_audit(journals),'old_usage':old_data['total_usage'],
            'old_model_attempts':old_data['actual_physical_provider_attempts'],
            'reported_token_change':base.usage_audit(journals)['known_tokens']/old_data['total_usage']['known_tokens']-1,
            'original_acquisition_snapshots_unchanged':all(base.sha(r['source'])==r['sha256'] for r in
                load(Path(inputs[0]['package']).parents[2]/'manifest.json')['original_inputs']),
            'parent_and_recovery_ledgers_unchanged':True,'paid_searches':0,'quota_resets':0,'submitted':False,
            'comparison_warning':'Supplemental material, input exposure, analyst schema and independent scoring changed together. No controlled model-only or predictive accuracy win is claimed.',
            'calibration_warning':'No sufficient verified first-release error pairs. Historical residual candidate does not exist for these four tasks; no distribution blend occurred.'}
        save(root/'report.json',report)
        print(json.dumps({k:v for k,v in report.items() if k not in ('questions','comparison_warning','calibration_warning')}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--prior',type=Path,required=True);p.add_argument('--recovery',type=Path,required=True)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--old-report',type=Path,required=True)
    p.add_argument('--complete-missing-decision',action='store_true');a=p.parse_args()
    run(a.root,a.prior,a.recovery,load(a.inputs),a.old_report,a.complete_missing_decision)
