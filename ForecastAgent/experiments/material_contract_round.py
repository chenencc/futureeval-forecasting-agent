"""Fixed identical-text material-fit replay and bounded paired model experiment."""
import argparse,hashlib,json,os
from pathlib import Path
from ForecastAgent.analysis.pilot import load,save,digest
from ForecastAgent.supplement import binding_guard
from ForecastAgent.supplement.material_contract import evaluate,ROLES,RELATIONS

MANIFEST=Path(__file__).with_name('MATERIAL_CONTRACT_REGRESSION.json')
BUDGET={'logical':20,'http':30,'output_tokens':1800}
MODEL='nvidia/nemotron-3-super-120b-a12b:free'


def legacy(case,observation):
    if observation is None:return {'status':'unreviewed'}
    guard=binding_guard.assess(case['legacy_need'],{'url':case['source']['url'],'quote':observation.get('quote','')})
    fits=all(observation.get('fit_axes',{}).get(a) is True for a in binding_guard.required_axes(case['legacy_need']))
    return {'status':'matched' if guard['eligible_for_material_closure'] and fits else 'mismatched','guard':guard}


def replay(output,manifest=MANIFEST):
    data=load(manifest);rows=[]
    for c in data['cases']:
        new=evaluate(c['contract'],c['source'],c['observation']);old=legacy(c,c['observation'])
        rows.append({'id':c['id'],'expected':c['expected'],'old':old,'new':new,
            'old_correct':old['status']==c['expected'],'new_correct':new['status']==c['expected']})
    report={'schema':'material-contract-replay-v1','manifest_sha256':hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        'cases':rows,'old_correct':sum(r['old_correct'] for r in rows),'new_correct':sum(r['new_correct'] for r in rows),
        'new_false_accepts':sum(r['new']['status']=='matched' and r['expected']!='matched' for r in rows),
        'new_false_rejects':sum(r['new']['status']!='matched' and r['expected']=='matched' for r in rows),
        'model_calls':0,'network_calls':0,'semantic_observations':'Human annotated; program replay is not a live model evaluation.'}
    save(Path(output)/'replay.json',report);return report


def paired(output,manifest=MANIFEST,key=None):
    from ForecastAgent.providers.model import ask_model
    from ForecastAgent.supplement.research_loop import decode,schema
    if os.environ.get('FORECAST_MODEL')!=MODEL or os.environ.get('FORECAST_MODEL_FALLBACK_SUPER')=='1':
        raise ValueError('paired_model_must_be_frozen_super_without_fallback')
    output=Path(output);data=load(manifest)
    identity={'manifest_sha256':hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),'model':MODEL,'budget':BUDGET,
        'arms':['legacy','contract'],'no_search_or_fetch':True,'frozen_cases':[c['id'] for c in data['cases']],
        'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if output.exists():raise ValueError('paired_output_exists_no_implicit_budget_reset')
    output.mkdir(parents=True);save(output/'identity.json',identity)
    state={'decisions':[],'attempts':[],'results':[]};save(output/'state.json',state)
    tool=schema('observe_material',{'document_role':{'type':'string','enum':list(ROLES)},
        'fit_axes':{'type':'object','properties':{a:{'type':'boolean'} for a in ('entity','material_type','metric','period')},
            'required':['entity','material_type','metric','period'],'additionalProperties':False},
        'evidence_relation':{'type':'string','enum':list(RELATIONS)},'quote':{'type':'string','maxLength':1200},
        'reason':{'type':'string','maxLength':240}},['document_role','fit_axes','evidence_relation','quote','reason'])
    def observer(event,record,token=None):
        if event=='reserve':
            if len(state['attempts'])>=BUDGET['http']:raise RuntimeError('paired_physical_budget_exhausted')
            if record['request']['model']!=MODEL:raise RuntimeError('paired_model_changed')
            token=len(state['attempts']);state['attempts'].append({'status':'reserved'})
        path=output/f'provider-{token+1:04d}.json';save(path,json.loads(json.dumps(record).replace(key,'[REDACTED]')) if key else record)
        state['attempts'][token]={'status':record['status'],'file':path.name,'usage':record.get('response',{}).get('usage')}
        save(output/'state.json',state);return token
    for index,case in enumerate(c for c in data['cases'] if c.get('model_eligible',True)):
        payload={k:case[k] for k in ('legacy_need','contract','source')}
        payload['sample_id']=f'sample-{index+1:03d}'
        # Both arms receive exactly the same raw text and metadata. Gold labels
        # and human observations stay outside the provider input.
        for arm in (['legacy','contract'] if index%2==0 else ['contract','legacy']):
            if len(state['decisions'])>=BUDGET['logical']:raise RuntimeError('paired_logical_budget_exhausted')
            row={'case_id':case['id'],'arm':arm,'status':'reserved','input_sha256':digest(payload)}
            state['decisions'].append(row);save(output/'state.json',state)
            prompt=('Assess the saved quote against legacy_need.condition and report all fit axes, document role, '
                    'evidence relation, a verbatim quote and a short reason. Do not invent context or use outside knowledge.')
            if arm=='contract':
                prompt=('Assess document material fit using the explicit contract. Outcome polarity must not affect fit. '
                    'A below-threshold score or unchanged rate remains a metric observation. A court opinion is not a docket; '
                    'methodology is not a historical snapshot. Missing source contract is unresolved, not absence. '
                    'Report boolean fit axes for entity, material_type, metric and period. material_type describes the '
                    'allowed document roles. Ignore nonrequired axes for closure. Infer role from the raw text; cite a '
                    'verbatim quote. Do not predict the event or use outside knowledge.')
            try:
                message=ask_model([{'role':'system','content':prompt},{'role':'user','content':json.dumps(payload)}],key,
                    tools=[tool],forced_tool='observe_material',observer=observer,max_output_tokens=1800,reasoning={'max_tokens':512})
                last=load(output/state['attempts'][-1]['file'])
                if (last.get('response',{}).get('choices') or [{}])[0].get('finish_reason')=='length':raise ValueError('paired_output_truncated')
                observation=decode(message,'observe_material');judgment=legacy(case,observation) if arm=='legacy' else evaluate(case['contract'],case['source'],observation)
                row.update(status='received',observation=observation,judgment=judgment,expected=case['expected'],correct=judgment['status']==case['expected'])
            except Exception as exc:
                row.update(status='failed',error=str(exc)[:240]);save(output/'state.json',state)
                if not isinstance(exc,ValueError):state['blocked']=True;break
            finally:save(output/'state.json',state)
        if state.get('blocked') or len(state['attempts'])>=BUDGET['http']:break
    report={'schema':'paired-material-contract-v1','identity':identity,'results':state['decisions'],
        'actual_http_attempts':len(state['attempts']),'known_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in state['attempts']),
        'unknown_usage_attempts':sum(not r.get('usage') for r in state['attempts']),
        'search_calls':0,'fetch_calls':0,'forecasts_submitted':0,'labels_sent_to_models':False,
        'scope':'Diagnostic material-fit cases selected from five repaired questions; not held-out collection or forecasting accuracy.'}
    save(output/'result.json',report);return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['replay','paired']);p.add_argument('--output',required=True);a=p.parse_args()
    r=replay(a.output) if a.mode=='replay' else paired(a.output,key=os.environ['OPENROUTER_API_KEY'])
    print(json.dumps({k:v for k,v in r.items() if k not in ('cases','results')}))
