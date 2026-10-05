"""Audit saved V2 arguments and report V3 control mechanics without providers."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ForecastAgent.acquisition.frontier_compare import inputs
from ForecastAgent.acquisition.pipeline import verify_baseline
from ForecastAgent.runtime.collection_actions import pending_passages, review_focus, next_action
from ForecastAgent.runtime.contracts import ContractError
from ForecastAgent.tests.test_material_protocol import (
    FIXTURE, saved, task_for, plan_for, review_state)


def report(source_root):
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    verified_records = set()
    cases = []
    for case in fixture['cases']:
        qid = case['id']
        directory = source_root/qid/'candidate'
        if hashlib.sha256((directory/'bundle.json').read_bytes()).hexdigest() != case['bundle_sha256']:
            raise ValueError('Original V2 bundle changed: '+qid)
        for row in case['calls']:
            path = directory/'model_calls'/row['record_name']
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['record_sha256']:
                raise ValueError('Original provider record changed: '+str(path))
            message = json.loads(raw)['response']['choices'][0]['message']
            actual = [(c['function']['name'], json.loads(c['function']['arguments']))
                      for c in message.get('tool_calls') or []]
            if (row['tool'], row['arguments']) not in actual:
                raise ValueError('Saved argument fixture differs from actual provider response')
            verified_records.add((qid,row['record_name']))
        with TemporaryDirectory() as root:
            task = task_for(qid,root)
            before = task.budget()
            original = copy.deepcopy(task.bundle['pages'])
            try:
                task.execute('plan_evidence',plan_for(qid),'')
                plan_result = {'accepted':True}
            except ContractError as exc:
                plan_result = {'accepted':False,'error':exc.details}
            cases.append({'question_id':qid, 'original_tool_calls':len(case['calls']),
                'v3_plan_check':plan_result,
                'translation':'Adds explicit rule_time_fields=[] only; target prose is unchanged.',
                'reservations_unchanged':task.budget()==before,
                'original_pages_unchanged':task.bundle['pages']==original})
    with TemporaryDirectory() as root:
        task = task_for('43991',root)
        task.execute('plan_evidence',plan_for('43991'),'')
        review_state(task,'43991')
        initial = len(pending_passages(task,limit=len(task.bundle['passages'])))
        batches = []
        for _ in range(2):
            focus = review_focus(task)
            batches.append({'spans':len(focus['passages']), 'text_chars':sum(len(r['text']) for r in focus['passages']),
                            'mandatory':focus['mandatory']})
            task.execute('review_passages',{'items':[{'passage_id':p['passage_id'],'action':'defer',
                'need_ids':[],'reason':'Control replay: preserve unresolved candidates; no relevance verdict.'}
                for p in focus['passages']]},'')
        court = {'original_review_requests':sum(r['tool']=='review_passages' for r in saved('43991')['calls']),
            'scripted_control_batches':batches,'initial_candidate_count':initial,
            'remaining_pending_count':len(pending_passages(task,limit=len(task.bundle['passages']))),
            'further_review_forced':bool(next_action(task) and next_action(task)['tool']=='review_passages'),
            'deferred_count':sum(r['action']=='defer' for r in task.bundle['passage_dispositions'].values()),
            'scripted_decision_scope':'Deferral demonstrates routing and retention only. No model decisions or semantic coverage gains are claimed.'}
    pool, rubric = inputs()
    return {'schema':'materials-v3-offline-control-audit-v1','parent_run_id':fixture['run_id'],
        'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        'verified_original_provider_records':len(verified_records),
        'original_pool_sha256':rubric['pool_sha256'],
        'source_count':sum(len(c['pages']) for c in pool['cases']),
        'frozen_release_dependencies_verified':len(verify_baseline()['initial_frozen_file_sha256']),
        'new_provider_http_attempts':0,'new_search_attempts':0,'forecast_submissions':0,
        'cases':cases,'court_queue_control':court,
        'stablecoin_control':{'saved_v2_requests':8,'replayed_request_decisions_to_export':6,
            'evidence':'test_stablecoin_saved_trace_exports_after_assessment_without_two_extra_requests',
            'translation':'Exact saved tool arguments; V3 explicitly adds missing_items=[] and rule_time_fields=[]. Raw legacy None remains in the audit record.',
            'scope':'Mocked request-loop mechanics, not a new paid model run or measured token reduction.'},
        'limitations':['No live quality/generalization result. V3 is opt-in; V1/V2 tasks are not migrated.',
            'Explicit date recognition covers ISO and English calendar dates, not all natural-language semantics.',
            'Rule-time dependencies and excerpt relevance remain agent declarations. Unknown omitted dependencies cannot be semantically certified.',
            'Deferred and unreviewed candidates remain recall limitations. Automatic export never implies factual verification.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = report(args.source_root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'verified_records':result['verified_original_provider_records'],
                      'new_provider_http_attempts':0}))


if __name__=='__main__':
    main()
