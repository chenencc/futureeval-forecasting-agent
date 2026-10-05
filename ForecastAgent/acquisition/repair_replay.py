"""Audit preserved failures and run zero-provider mechanical repair regressions."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import unittest

from ForecastAgent.acquisition.pipeline import verify_baseline
from ForecastAgent.tests.test_intelligent_repairs import (
    IntelligentRepairTests, saved_cases, FIXTURE)


def audit(source_root):
    cases = []
    for case in saved_cases():
        root = source_root/case['id']/'candidate'
        bundle_raw = (root/'bundle.json').read_bytes()
        if hashlib.sha256(bundle_raw).hexdigest() != case['bundle_sha256']:
            raise ValueError('Preserved bundle changed: '+case['id'])
        for call in case['calls']:
            raw = (root/'model_calls'/call['record_name']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != call['record_sha256']:
                raise ValueError('Preserved provider record changed: '+case['id'])
            message = json.loads(raw)['response']['choices'][0]['message']
            def decode(value):
                try:
                    return json.loads(value)
                except (ValueError, TypeError):
                    return value
            if not any(c['function']['name']==call['tool'] and
                       decode(c['function']['arguments'])==call['arguments']
                       for c in message.get('tool_calls',[])):
                raise ValueError('Replay fixture differs from actual response')
        old_visibility = []
        if case['id']=='36871':
            for path in sorted((root/'model_calls').glob('*.json')):
                record = json.loads(path.read_text(encoding='utf-8'))
                text = json.dumps(record['request']['messages'], ensure_ascii=False)
                old_visibility.append({'record':path.name,
                    'both_stablecoin_values_in_actual_request':all(v in text for v in
                        ('$184,368,461,962.97','$73,360,220,193.95'))})
        cases.append({'question_id':case['id'], 'preserved_bundle_sha256':case['bundle_sha256'],
                      'actual_v1_plan_failures':case['original_plan_failures'],
                      'saved_arguments_verified':len(case['calls']),
                      'v1_stablecoin_request_visibility':old_visibility})
    log = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IntelligentRepairTests)
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise ValueError('Mechanical repair regression failed:\n'+log.getvalue())
    base = verify_baseline()
    return {'schema':'intelligent-materials-v2-offline-repair-v1',
            'source_run_id':37288399817, 'physical_model_requests':0,
            'physical_search_requests':0, 'physical_source_requests':0,
            'analysis_requests':0, 'submissions':0, 'production_changed':False,
            'baseline_release':base['baseline_release'],
            'frozen_dependency_count':len(base['initial_frozen_file_sha256']),
            'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            'cases':cases, 'tests_run':result.testsRun, 'tests_passed':True,
            'regression_log':log.getvalue(),
            'observed_mechanics':{
                'five_saved_typed_plans':'Test-only field-handle translation preserves all original conditions and copies exact original fields. It is not a new agent decision.',
                'failed_response_sequences':{'43494':{'old_actual_failures':10,'new_simulated_responses_before_closure':2},
                                            '44801':{'old_actual_failures':5,'new_simulated_responses_before_closure':2}},
                'loop_test_scope':'Unadapted legacy responses are invalid under the new wire protocol; this tests bounded failure closure, not how a model would correct the new request.',
                'stablecoin_table':'Both actual saved values survive three neutral catalog groups under an 18000-character ceiling, then bank by exact version-bound passage IDs.',
                'retention_limit':'At most two spans and 6000 exact text characters per review focus; unresolved spans remain on disk and forced closure exports their count.',
                'safety':'Explicit rejection works, stale versions disappear from selectable candidates, plan failure count and provider reservations survive restore, and V1-to-V2 task identity changes are rejected.'},
            'limitations':['No new inference was performed; model request count, tokens, source selection, adequacy assessments and recall gains remain unmeasured for V2.',
                           'Original field binding is structural; correct entity, metric, dates and exceptions still require agent judgment.',
                           'Frozen downstream analysis is unchanged; better banking alone does not demonstrate better forecast inputs or scores.',
                           'Current historical bodies remain exploratory and potentially contaminated by future information.'],
            'promotion_allowed':False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    report = audit(args.source_root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('tests_run','tests_passed','physical_model_requests',
                                         'frozen_dependency_count','promotion_allowed')}))


if __name__=='__main__':
    main()
