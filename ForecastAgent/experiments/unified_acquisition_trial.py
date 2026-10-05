"""Fresh bounded acquisition packages connected to the frozen V7 reviewer."""
import argparse
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path

from ForecastAgent.acquisition import collect
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.experiments import mercury_field_scopes_trial as review
from ForecastAgent.runtime.task_lock import task_lock

MANIFEST = Path(__file__).with_name('UNIFIED_ACQUISITION5.json')


def run(output, manifest=MANIFEST, *, resume=None):
    data = load(manifest)
    cases = data['cases']
    if not 1 <= len(cases) <= 5 or len({c['question_id'] for c in cases}) != len(cases):
        raise ValueError('one_to_five_unique_questions_required')
    for case in cases:
        if set(case) != {'id', 'question_id', 'request', 'plan'}:
            raise ValueError('only_question_and_frozen_requirements_may_seed_fresh_collection')
    for filename, expected in data['integration_code_sha256'].items():
        if hashlib.sha256((review.ROOT / filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise ValueError('integration_code_changed')
    output = Path(output)
    if output.exists():
        raise ValueError('existing_trial_cannot_reset_budget')
    if os.environ.get('FORECAST_SHARED_CACHE_ROOT'):
        raise ValueError('fresh_trial_forbids_cross_task_body_cache')
    identity = {'manifest_sha256': digest(data), 'trial_id': data['trial_id']}
    if resume:
        parent = Path(resume)
        if load(parent / 'identity.json') != identity:
            raise ValueError('wrong_resume_parent')
        shutil.copytree(parent, output)
    else:
        output.mkdir(parents=True)
    save(output / 'identity.json', identity)
    save(output / 'manifest.json', data)
    with task_lock(output):
        state_path = output / 'state.json'
        state = load(state_path) if state_path.exists() else {'trial_id': data['trial_id'], 'cases': [],
            'independent_new_budgets': True, 'old_ledgers_reset': False, 'forecast_submissions': 0}
        review_cases = []
        for case in cases:
            row = next((r for r in state['cases'] if r['id'] == case['id']), None)
            if row is None:
                row = {'id': case['id'], 'question_id': case['question_id'], 'stage': 'acquisition'}
                state['cases'].append(row)
            save(state_path, state)
            folder = output / 'tasks' / str(case['question_id'])
            print(json.dumps({'case': case['id'], 'stage': 'acquisition'}, ensure_ascii=False), flush=True)
            try:
                package = collect(case['request'], folder, limits=data['repair_limits'])
                bundle = load(folder / 'analysis-input.json')
                row.update(stage='review_pending', acquisition=package)
                row.pop('error', None)
                if not bundle.get('pages'):
                    row.update(stage='review_no_readable_material', error='No readable body; review was not attempted.')
                else:
                    review_cases.append({'id': case['id'], 'question_id': case['question_id'],
                        'bundle': bundle, 'plan': case['plan'], 'arm_order': ['v7']})
            except Exception as exc:
                row.update(stage='acquisition_failed', error=str(exc)[:300])
            save(state_path, state)
            print(json.dumps({'case': case['id'], 'stage': row['stage'],
                'readable_pages': row.get('acquisition', {}).get('readable_unique_pages'),
                'error': row.get('error')}, ensure_ascii=False), flush=True)
        if review_cases:
            review_manifest = {'trial_id': data['trial_id'] + '-v7', 'arms': ['v7'], 'cases': review_cases,
                'limits': data['review_limits'], 'prior_experiments': data['prior_experiments'],
                'frozen_code_sha256': data['review_code_sha256']}
            path = output / 'review-manifest.json'
            if path.exists() and load(path) != review_manifest:
                raise ValueError('review_parent_changed_use_a_separate_trial')
            save(path, review_manifest)
            previous = state.get('review_directory')
            if previous and (output / previous / 'result.json').exists() and load(output / previous / 'result.json')['business_gate_passed']:
                result = load(output / previous / 'result.json')
            else:
                index = 1
                while (output / f'review-{index:03d}').exists():
                    index += 1
                name = f'review-{index:03d}'
                # The exact parent retains all Mercury reservations on continuation.
                state['review_directory'] = name
                save(state_path, state)
                result = review.run(output / name, os.environ['OPENROUTER_API_KEY'], path,
                                    resume=output / previous if previous else None)
            for reviewed in result['cases']:
                row = next(r for r in state['cases'] if r['id'] == reviewed['case_id'])
                row['review'] = reviewed['arms'].get('v7')
                row['stage'] = 'complete' if reviewed['pair_complete'] else 'review_failed'
            state['review_summary'] = {k: result[k] for k in ('actual_http_attempts', 'logical_decisions', 'blocked', 'pending_case_ids')}
        state['complete'] = len(state['cases']) == len(cases) and all(r['stage'] == 'complete' for r in state['cases'])
        save(state_path, state)
        save(output / 'result.json', state)
        return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--manifest', default=str(MANIFEST))
    parser.add_argument('--resume')
    args = parser.parse_args()
    result = run(args.output, args.manifest, resume=args.resume)
    print(json.dumps({'complete': result['complete'], 'states': [r['stage'] for r in result['cases']]}))
    if not result['complete']:
        raise SystemExit(1)
