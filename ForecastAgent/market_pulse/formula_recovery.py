"""One bounded formula repair; retain prior decisions and all spent attempts."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import time

from ForecastAgent.market_pulse import formula_trial as trial, formulas as f, error_distribution as errors
from ForecastAgent.market_pulse import financial_chain as old, analysis as base
from ForecastAgent.market_pulse.financial_recovery import ledger_hashes
from ForecastAgent.analysis.pilot import Journal,digest,load,save
from ForecastAgent.providers import ultra
from ForecastAgent.competition.mercury import distribution_spec
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.releases.v1_0_5 import verify_release,validate_payload
from ForecastAgent.runtime.task_lock import task_lock

VERSION='market-pulse-formula-recovery-v1'


def tool_for(variables):
    tool=copy.deepcopy(f.TOOL);schema=tool['function']['parameters'];ids=[v['fact_id'] for v in variables]
    schema['properties']['assumptions']['items']['properties']['fact_refs']['items']={'type':'string','enum':ids}
    refs=ids+[f'A{i}' for i in range(1,6)]+[f'C{i}' for i in range(1,13)]
    schema['properties']['calculations']['items']['properties']['inputs']['items']={'type':'string','enum':refs}
    schema['properties']['central_ref']={'type':'string','enum':[f'C{i}' for i in range(1,13)]}
    schema['properties']['scenarios']['items']['properties']['calculation_ref']={'type':'string','enum':ids+[f'C{i}' for i in range(1,13)]}
    return tool


def repair(state,variables,target,prior,folder):
    consumed=len(list((prior/'super/http').glob('*.json')))
    cap=1 if consumed else 2
    if consumed>2:raise ValueError('Parent formula cap exceeded')
    if not consumed:
        return trial.analyst(state,variables,target,folder/'super'),cap,consumed
    tool=tool_for(variables)
    source=load(sorted((prior/'super/http').glob('*.json'))[-1])
    bad=source.get('response',{}).get('choices',[{}])[0].get('message',{}).get('tool_calls',[])
    prompt={'original_inputs':state,'previous_failure':load(prior/'analyst-error.json'),
        'previous_formulas':bad,'instruction':'One final compact repair over the same original documents. Background values now have original-bound V IDs with dates UNKNOWN; do not invent their fiscal quarters. Prefer target-quarter guidance over unsupported historical extrapolation. No copied actual value in A assumptions. Never use zero multiplication to represent an unchanged value.'}
    messages=[{'role':'system','content':f.SYSTEM},{'role':'user','content':json.dumps(prompt)}]
    identity={'messages':digest(messages),'tool':digest(tool),'prior_super_requests':{p.name:base.sha(p) for p in (prior/'super/http').glob('*.json')},
        'prior_spent':consumed,'additional_cap':cap,'cumulative_cap':consumed+cap,'model':old.MODEL}
    if (folder/'repair/identity.json').exists() and load(folder/'repair/identity.json')!=identity:raise ValueError('Frozen compact repair changed')
    save(folder/'repair/identity.json',identity);save(folder/'repair/messages.json',messages)
    journals=sorted((folder/'repair/http').glob('*.json'))
    if journals:response=load(journals[0]).get('response',{}).get('choices',[{}])[0].get('message',{})
    else:response=ultra.ask_ultra(messages,os.environ['OPENROUTER_API_KEY'],tools=[tool],forced_tool='record_financial_formulas',
            observer=Journal(folder/'repair/http',cap),max_output_tokens=2600,reasoning={'max_tokens':600},
            require_tool=True,deadline=time.monotonic()+180)
    calls=response.get('tool_calls',[])
    if len(calls)!=1 or calls[0].get('function',{}).get('name')!='record_financial_formulas':raise ValueError('Compact repair missing tool output')
    raw=json.loads(calls[0]['function']['arguments']);save(folder/'repair/raw.json',raw)
    result=f.evaluate(raw,variables,target);save(folder/'repair/evaluated.json',result)
    return result,cap,consumed


def run(root,prior,inputs):
    if os.environ.get('METACULUS_TOKEN'):raise ValueError('No platform credential allowed')
    verify_release();root.mkdir(parents=True,exist_ok=True)
    parent=load(prior/'manifest.json')
    for row in parent['inputs']:
        for field in ('package','variables','error_pairs','coverage'):
            if base.sha(row[field])!=row['sha256'][field]:raise ValueError('Frozen initial source changed: '+row['id']+' '+field)
    manifest={'protocol':VERSION,'parent':str(prior),'parent_ledger_hashes':ledger_hashes(prior),'inputs':inputs,
        'code':{Path(p).name:base.sha(p) for p in (__file__,f.__file__,errors.__file__)},
        'super_cumulative_cap':3,'mercury_cumulative_cap':1,'new_acquisition_calls':0,'quota_resets':0,'submitted':False}
    with task_lock(root):
        if (root/'manifest.json').exists() and load(root/'manifest.json')!=manifest:raise ValueError('Frozen continuation changed')
        save(root/'manifest.json',manifest)
        for path in (__file__,f.__file__,errors.__file__):(root/('executed-'+Path(path).name)).write_bytes(Path(path).read_bytes())
        rows=[]
        for row in inputs:
            folder=root/'tasks'/row['id'];folder.mkdir(parents=True,exist_ok=True)
            save(root/'progress.json',{'status':'running','active_id':row['id'],'finished_ids':[r['id'] for r in rows]})
            try:
                for field in ('package','variables','error_pairs','coverage'):
                    if base.sha(row[field])!=row['sha256'][field]:raise ValueError('Frozen continuation input changed')
                bundle=load(row['package']);variables=load(row['variables']);f.validate_variables(bundle,variables)
                contract=base.contract(bundle['request']);question,selection=f.scoped_question(bundle)
                reduced=copy.deepcopy(contract);reduced.pop('platform_metadata',None)
                state={'original_question':question,'question_selection_audit':selection,'financial_contract':reduced,
                    **f.compact_catalog(variables),'source_coverage':load(row['coverage']),
                    'warning':'Numeric binding is checked; source roles, columns and calendar/fiscal interpretations remain reviewed claims. Unknown future target outcome. No original text changed.'}
                size=len(json.dumps(state).encode());save(folder/'state.json',state)
                if size>30000:raise ValueError('Bounded target-scoped context still too large: '+str(size))
                unit='USD_per_share' if contract['metric']=='gaap_diluted_eps' else 'USD'
                result_file=folder/'result.json'
                if result_file.exists():result=load(result_file)
                else:
                    derivation,cap,spent=repair(state,variables,unit,prior/'tasks'/row['id'],folder)
                    old_result_file=prior/'tasks'/row['id']/'result.json'
                    if old_result_file.exists():
                        result=copy.deepcopy(load(old_result_file));result['decision_reused_from']=str(old_result_file)
                        result['decision_reused_sha256']=base.sha(old_result_file)
                        result['decision_input_policy']='Prior valid independent full-question original-evidence decision retained unchanged; no claim of identical request to this compact analyst view.'
                    else:
                        spec=distribution_spec(bundle['request']);registry=old.decision_registry(spec)
                        registry['event_outcome']['instructions']='Forecast the FIRST future GAAP/total-quarter target quantity from ORIGINAL evidence. No Super forecast is supplied. Guidance is not a probability interval; one-offs need recurrence scenarios; preserve open tails and keep diagnostic confidence separate.'
                        registry['interpretation_consistent']['instructions']='Review original fiscal periods, GAAP basis, units and table columns of the variable interpretations. Check date-unknown background values without inventing quarters.'
                        registry['material_conflict']['instructions']='Do original sources materially contradict a variable interpretation? Historical periods and future assumptions are distinct.'
                        decision=copy.deepcopy(state);decision['financial_contract']=contract
                        response=base.chain.call(decision,folder/'mercury-independent',registry)
                        raw=typed.forecast(response,spec);candidate=payload(bundle['request'],{'continuous_cdf':raw['raw_cdf']})
                        validate_payload(bundle['request'],candidate)
                        result={'independent_mercury_raw':raw,'payload':candidate,'mercury_answers':response['answers'],
                            'quantiles':{str(p):base.quantile(candidate['continuous_cdf'],bundle['request'],p) for p in (.1,.5,.9)}}
                    error_fit=errors.fit(load(row['error_pairs']),row['as_of'],contract['metric'],unit,row['error_family'],derivation['central_value'],bundle['request'])
                    result.update(protocol=VERSION,id=row['id'],issuer=contract['issuer'],target_period=contract['target_period'],
                        status='completed_with_calibration_gap',formula=derivation,formula_error=None,
                        error_based_candidate=error_fit,variable_count=len(variables),context_bytes=size,
                        parent_super_attempts=spent,additional_super_cap=cap,mercury_blind_to_super_output=True,
                        source_numeric_and_quote_binding_valid=True,delivery_ready=False,submitted=False,
                        forward_targets_unresolved=True,forecast_accuracy_not_evaluated=True)
                    save(result_file,result)
                rows.append({k:result[k] for k in ('id','issuer','status','formula','quantiles','context_bytes','variable_count')})
                print(json.dumps({k:v for k,v in rows[-1].items() if k not in ('formula','quantiles')}),flush=True)
            except Exception as exc:
                failure={'id':row['id'],'status':'failed','error':str(exc),'error_type':type(exc).__name__,'state_preserved':True}
                save(folder/'failure.json',failure);rows.append(failure);print(json.dumps(failure),flush=True)
        if ledger_hashes(prior)!=manifest['parent_ledger_hashes']:raise ValueError('Parent run ledger changed')
        journals=[load(p) for r in (prior,root) for p in (r/'tasks').glob('*/super/http/*.json')]
        journals += [load(p) for p in (root/'tasks').glob('*/repair/http/*.json')]
        mercury=[load(p) for r in (prior,root) for p in (r/'tasks').glob('*/mercury-independent/http/*.json')]
        transport=[load(p) for r in (prior,root) for p in (r/'provider-transport/openrouter/attempts').glob('*.json')]
        summary={'protocol':VERSION,'status':'finished','rows':rows,'cumulative_super_attempts':len(journals),
            'cumulative_mercury_attempts':len(mercury),'cumulative_physical_attempts':len(transport),
            'cumulative_super_usage':base.usage_audit(journals),'cumulative_mercury_usage':base.usage_audit(mercury),
            'cumulative_usage':base.usage_audit(journals+mercury),'parent_ledger_unchanged':True,
            'quota_resets':0,'new_acquisition_calls':0,'submitted':False}
        save(root/'summary.json',summary);save(root/'progress.json',summary)
        print(json.dumps({k:v for k,v in summary.items() if k!='rows'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--prior',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,required=True)
    args=parser.parse_args();run(args.root,args.prior,load(args.inputs))
