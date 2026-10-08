"""Local financial strengthening trial: formula Super and independent Mercury.

Both read the same frozen reviewed variables. Mercury never sees the Super
center or scenarios. An error-based candidate is separately gated by history.
No Metaculus credential, acquisition calls, production changes or submissions.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import time

if os.environ.get('FORECAST_PROVIDER_FAILOVER_MODULE'):
    import runpy
    runpy.run_path(os.environ['FORECAST_PROVIDER_FAILOVER_MODULE'])['install']()

from ForecastAgent.market_pulse import analysis as base, financial_chain as old, formulas, error_distribution as errors
from ForecastAgent.analysis.pilot import Journal,digest,load,save
from ForecastAgent.providers import ultra
from ForecastAgent.providers.model import configured_model
from ForecastAgent.competition.mercury import distribution_spec
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.releases.v1_0_5 import verify_release,validate_payload
from ForecastAgent.runtime.task_lock import task_lock

VERSION='market-pulse-financial-strengthening-v1'


def analyst(state,variables,target_unit,folder):
    identity={'state':digest(state),'tool':digest(formulas.TOOL),'system':digest(formulas.SYSTEM),
        'model':old.MODEL,'physical_reservation_cap':2,'output_tokens':2600,'reasoning_tokens':600}
    if (folder/'identity.json').exists() and load(folder/'identity.json')!=identity:raise ValueError('Frozen formula request changed')
    save(folder/'identity.json',identity);save(folder/'state.json',state)
    if (folder/'raw.json').exists():return formulas.evaluate(load(folder/'raw.json'),variables,target_unit)
    if configured_model()!=old.MODEL:raise ValueError('This trial requires the authorized fixed Super model')
    messages=[{'role':'system','content':formulas.SYSTEM},{'role':'user','content':json.dumps(state)}]
    journal=Journal(folder/'http',2);previous=sorted((folder/'http').glob('*.json'))
    def parse(response):
        calls=response.get('tool_calls',[])
        if len(calls)!=1 or calls[0].get('function',{}).get('name')!='record_financial_formulas':raise ValueError('Missing formula tool output')
        raw=json.loads(calls[0]['function']['arguments']);result=formulas.evaluate(raw,variables,target_unit)
        save(folder/'raw.json',raw);save(folder/'evaluated.json',result);return result
    last_error=None;last_bad=None
    for p in previous:
        body=load(p).get('response',{}).get('choices',[{}])[0].get('message',{})
        try:return parse(body)
        except (ValueError,KeyError,TypeError) as exc:last_error=str(exc);last_bad=body
    for _ in range(2-len(previous)):
        current=copy.deepcopy(messages)
        if last_error:current.append({'role':'user','content':'Program validation rejected this output: '+last_error+'. Prior output: '+json.dumps(last_bad)+'. Return a complete corrected formula tool result over the same inputs.'})
        save(folder/'messages.json',current)
        response=ultra.ask_ultra(current,os.environ['OPENROUTER_API_KEY'],tools=[formulas.TOOL],
            forced_tool='record_financial_formulas',observer=journal,max_output_tokens=2600,
            reasoning={'max_tokens':600},require_tool=True,deadline=time.monotonic()+180)
        try:return parse(response)
        except (ValueError,KeyError,TypeError) as exc:
            last_error=str(exc);last_bad=response
            save(folder/f'validation-error-{len(list((folder/"http").glob("*.json")))}.json',{'error':last_error})
    raise ValueError('Formula attempt cap reached: '+str(last_error))


def task(root,source,old_result):
    folder=root/'tasks'/source['id'];folder.mkdir(parents=True,exist_ok=True)
    with task_lock(folder):
        if (folder/'result.json').exists():return load(folder/'result.json')
        bundle=load(source['package']);variables=load(source['variables']);pairs=load(source['error_pairs'])
        formulas.validate_variables(bundle,variables)
        contract=base.contract(bundle['request']);short_contract=copy.deepcopy(contract);short_contract.pop('platform_metadata',None)
        state={'original_question':{k:bundle['request'].get(k,'') for k in base.RULE_FIELDS},
            'financial_contract':short_contract,'variables':formulas.compact(variables),
            'source_coverage':load(source['coverage']),
            'warning':'Reviewed selection and semantics remain fallible; all originals are separately archived. No target resolution supplied.'}
        save(folder/'shared-material-state.json',state)
        if len(json.dumps(state).encode())>30000:raise ValueError('Compact financial state exceeds predeclared bound')
        target_unit='USD_per_share' if contract['metric']=='gaap_diluted_eps' else 'USD'
        derivation=None;analysis_error=None
        try:derivation=analyst(state,variables,target_unit,folder/'super')
        except Exception as exc:
            analysis_error={'type':type(exc).__name__,'error':str(exc)};save(folder/'analyst-error.json',analysis_error)
        # Independent original-evidence scoring: no Super output is visible.
        spec=distribution_spec(bundle['request']);registry=old.decision_registry(spec)
        registry['event_outcome']['instructions']='Forecast the FIRST future target-quarter quantity in the original question from these original sources and reviewed canonical variable interpretations. No analyst forecast is supplied. Management guidance is not a probability interval. Account for seasonality, GAAP one-offs and unknown future changes. Preserve open tails; platform bounds are not a prior or consensus. Diagnostic confidence is not outcome probability.'
        registry['interpretation_consistent']['instructions']='Do the supplied source-bound variable interpretations respect original issuer, fiscal period, total quarterly metric, GAAP basis, table columns and units? Review the original quoted context independently.'
        registry['material_conflict']['instructions']='Do the supplied original sources contradict a variable value or its interpreted metric, period, column, unit or GAAP basis? Historical differences and explicit forecast assumptions are not contradictions.'
        mercury_state=copy.deepcopy(state);mercury_state['financial_contract']=contract
        save(folder/'mercury-input.json',mercury_state)
        response=base.chain.call(mercury_state,folder/'mercury-independent',registry)
        raw=typed.forecast(response,spec);candidate=payload(bundle['request'],{'continuous_cdf':raw['raw_cdf']})
        validate_payload(bundle['request'],candidate)
        b=errors.fit(pairs,source['as_of'],contract['metric'],target_unit,source['error_family'],
            derivation['central_value'] if derivation else 0,bundle['request'])
        if b.get('raw_cdf'):b['payload']=payload(bundle['request'],{'continuous_cdf':b['raw_cdf']});validate_payload(bundle['request'],b['payload'])
        old_data=load(old_result/'tasks'/source['id']/'result.json')
        result={'protocol':VERSION,'id':source['id'],'issuer':contract['issuer'],'target_period':contract['target_period'],
            'status':'completed_with_calibration_gap' if derivation and b['cdf'] is None else 'completed_requires_validation' if derivation else 'independent_score_only',
            'formula':derivation,'formula_error':analysis_error,'independent_mercury_raw':raw,'payload':candidate,
            'mercury_answers':response['answers'],'mercury_blind_to_super_output':True,
            'quantiles':{str(p):base.quantile(candidate['continuous_cdf'],bundle['request'],p) for p in (.1,.5,.9)},
            'error_based_candidate':b,'source_numeric_and_quote_binding_valid':True,'variable_count':len(variables),
            'old_quantiles':old_data['quantiles'],'old_analyst_forecast':old_data['analyst_forecast'],
            'forward_targets_unresolved':True,'forecast_accuracy_not_evaluated':True,'delivery_ready':False,'submitted':False,
            'comparison_warning':'Enriched material, simpler analyst schema and independent decision exposure changed together. Mechanism comparison, not measured predictive improvement.'}
        save(folder/'result.json',result);return result


def run(root,inputs,old_result):
    if os.environ.get('METACULUS_TOKEN'):raise ValueError('No platform credential permitted')
    verify_release();root.mkdir(parents=True,exist_ok=True)
    module_hashes={Path(p).name:base.sha(p) for p in (__file__,formulas.__file__,errors.__file__)}
    manifest={'protocol':VERSION,'inputs':inputs,'old_result':str(old_result),'code':module_hashes,
        'model':old.MODEL,'decision_model':'inception/mercury-decide:free','super_http_cap':2,'mercury_http_cap':1,
        'paid_searches':0,'submitted':False,'previous_ledgers_not_reset':True}
    with task_lock(root):
        if (root/'manifest.json').exists() and load(root/'manifest.json')!=manifest:raise ValueError('Frozen trial identity changed')
        save(root/'manifest.json',manifest)
        for path in (__file__,formulas.__file__,errors.__file__):(root/('executed-'+Path(path).name)).write_bytes(Path(path).read_bytes())
        rows=[]
        for source in inputs:
            for field in ('package','variables','error_pairs','coverage'):
                if base.sha(source[field])!=source['sha256'][field]:raise ValueError('Frozen trial material changed')
            save(root/'progress.json',{'status':'running','active_id':source['id'],'finished_ids':[r['id'] for r in rows]})
            try:
                result=task(root,source,old_result)
                row={k:result[k] for k in ('id','issuer','status','formula','quantiles','variable_count','delivery_ready')}
                row['calibration_status']=result['error_based_candidate']['status']
            except Exception as exc:
                row={'id':source['id'],'status':'failed','error_type':type(exc).__name__,'error':str(exc),'state_preserved':True}
                save(root/'tasks'/source['id']/'failure.json',row)
            rows.append(row);print(json.dumps({k:v for k,v in row.items() if k not in ('formula','quantiles')}),flush=True)
        super_records=[load(p) for p in (root/'tasks').glob('*/super/http/*.json')]
        mercury_records=[load(p) for p in (root/'tasks').glob('*/mercury-independent/http/*.json')]
        transport=[load(p) for p in (root/'provider-transport/openrouter/attempts').glob('*.json')]
        summary={'protocol':VERSION,'status':'finished','rows':rows,'super_attempts':len(super_records),
            'mercury_attempts':len(mercury_records),'super_usage':base.usage_audit(super_records),
            'mercury_usage':base.usage_audit(mercury_records),'physical_provider_attempts':len(transport),
            'known_usage':base.usage_audit(super_records+mercury_records),'paid_searches':0,'submitted':False}
        save(root/'summary.json',summary);save(root/'progress.json',summary)
        print(json.dumps({k:v for k,v in summary.items() if k!='rows'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--old-result',type=Path,required=True)
    args=parser.parse_args();run(args.root,load(args.inputs),args.old_result)
