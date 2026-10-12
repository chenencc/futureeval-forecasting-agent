"""Append-only oracle audit and zero-provider original-score fallback replay."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from collections import Counter

from ForecastAgent.evals import cases
from ForecastAgent.runtime.records import load,save
from ForecastAgent.providers.decisions import validate
from ForecastAgent.research_loop import forecast_brief_refs


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(root,destination):
    root,destination=Path(root),Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    files=[root/'report.json',root/'manifest.json',root/'gold-private.json',root/'cases.json']
    files+=list((root/'http').glob('*.json'))+list((root/'jobs').glob('*/result.json'))
    before={str(p):sha(p) for p in files}
    report=load(root/'report.json')
    results=[load(p) for p in (root/'jobs').glob('*/result.json')]
    comparisons=[]
    # The ambiguous support-vs-event family cannot establish a prompt gain.
    for model in ('super','mercury'):
        for prompt in ('P0','P1'):
            rows=[r for r in results if r['job']['batch']=='A' and r['job']['model']==model
                  and r['job']['prompt']==prompt and r['grade']['category']!='missing_evidence']
            comparisons.append({'model':model,'prompt':prompt,'unambiguous_controlled_cases':len(rows),
                'correct':sum(r['grade']['answer_correct'] for r in rows),
                'exclusion_reason':'Does the report establish attendance? admits NO as an epistemic answer while the old oracle requires UNKNOWN as event status.',
                'posthoc_diagnostic_only':True,'independent_holdout_improvement_proven':False})
    disputed=[]
    for r in results:
        g=r.get('grade',{})
        if g and not g['source_correct'] and g['gold']['basis']=='unknown' and g['selected']['source_id']=='S1':
            disputed.append({'job_id':r['job']['id'],'selected':g['selected'],
                'reason':'A source describing missing reporting may legitimately be cited; old NONE-only oracle is too strict. No automatic semantic certification.'})
    parents={p['id']:p for p in load(root/'manifest.json')['parents']}
    fallback=[]
    for ident,parent in parents.items():
        a=load(root/'jobs'/f'E-{ident}-mercury-original'/'result.json')
        b=load(root/'jobs'/f'E-{ident}-mercury-brief'/'result.json')
        common=load(root/'real'/ident/'common.json')
        request=load(root/'jobs'/a['job']['id']/'request.json')
        receipt=load(root/'http'/f'{a["http_record_id"]:04}.json')
        if request['state']!=common or a['original_sha256']!=cases.sha(common):
            raise ValueError('Original forecast source identity mismatch')
        wire=hashlib.sha256(json.dumps(request).encode()).hexdigest()
        if wire!=receipt['request_sha256'] or receipt['status']!='received':
            raise ValueError('Original request wire or receipt mismatch')
        validate(receipt['response'],request['questions'])
        if b['status']=='held_failed_brief':
            question=json.loads(Path(parent['path']).read_text(encoding='utf-8-sig'))['request']
            with patch('socket.create_connection',side_effect=AssertionError('Offline replay must not open a network connection')),patch.object(forecast_brief_refs.decision_http,'call',return_value=receipt['response']) as decision:
                projected=forecast_brief_refs.score(question,common,request['questions'],None,
                    destination/'fallback'/ident,brief=None)
            if projected['payload']['probability_yes']!=a['probability_yes_clipped']:
                raise ValueError('Fallback changed an existing original-only forecast')
            fallback.append({'id':ident,'status':'original_only_fallback_replay',
                'original_response_reused':True,'independent_comparison_arm':False,
                'original_forecast_clock':a['forecast_created_at'],
                'payload':projected['payload'],'wire_sha256':wire,
                'new_model_http':0,'client_reply_injected_from_verified_receipt':True})
    after={str(p):sha(p) for p in files}
    if before!=after:raise ValueError('Frozen campaign was mutated')
    final={'schema':'capability-assimilation-audit-v1','parent_report':str(root/'report.json'),
        'parent_report_sha256':before[str(root/'report.json')],
        'original_report_preserved':True,'source_files_checked':len(files),
        'ambiguous_family_excluded_comparisons':comparisons,
        'disputed_source_labels':disputed,
        'brief_contract_failures':Counter(r.get('error_type') for r in results if r['status']=='invalid_output'),
        'fallback_replays':fallback,'original_only_forecasts_available':12,
        'actual_paired_forecasts_available':5,'new_model_http':0,
        'development_changes':[
            'Model-neutral event-versus-evidence reading policy.',
            'Reference-ID brief is the default for new development brief trials.',
            'Failed optional brief reuses the identity-checked original decision cache.',
            'Correct future fixture wording and allow references explaining unknown event status.'],
        'new_prompt_provider_validated':False,'forecast_improvement_proven':False,
        'production_promoted':False,'submitted':False}
    save(destination/'report.json',final)
    return final


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--destination',required=True)
    args=parser.parse_args();result=audit(args.root,args.destination)
    print(json.dumps({'new_http':0,'fallback_replays':len(result['fallback_replays']),
        'source_files_checked':result['source_files_checked'],'original_report_preserved':True,
        'unambiguous_comparisons':result['ambiguous_family_excluded_comparisons']}))


if __name__=='__main__':main()
