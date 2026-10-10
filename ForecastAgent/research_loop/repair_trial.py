"""Bounded repair allocation over immutable evidence; reuse exact earlier A receipts."""
import argparse
from pathlib import Path
import shutil

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.research_loop import live_trial, decision
from ForecastAgent.research_loop.state import now


def prepare(source, ids, root, previous_repair=None, super_http_cap=2):
    source, root = Path(source), Path(root)
    cohort = load(source/'cohort.json')
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Explicit unique repair IDs are required')
    rows = {str(r['id']): r for r in cohort['cases']}
    plan = {'protocol': 'saved-evidence-stage-repair-v1', 'frozen_at_utc': None,
        'source_cohort': str(source.resolve()), 'source_cohort_sha256': live_trial.sha(source/'cohort.json'),
        'implementation': decision.implementation_hashes(), 'cases': [],
        'previous_repair': str(Path(previous_repair).resolve()) if previous_repair else None,
        'previous_repair_plan_sha256': live_trial.sha(Path(previous_repair)/'repair-plan.json') if previous_repair else None,
        'allocation': {'super_http_per_case': super_http_cap, 'new_mercury_enriched_http_per_case': 1,
            'new_mercury_baseline_http': 0, 'search_http': 0, 'fetch_http': 0},
        'old_budgets_reset': False, 'old_attempts_preserved': True, 'submitted': False,
        'comparison_limit': 'Earlier A versus repaired B is staggered in time, not a fresh contemporaneous pair.'}
    imports = root/'baseline-imports'
    if type(super_http_cap) is not int or not 1 <= super_http_cap <= 2:
        raise ValueError('Repair allocation must be one or two physical Super requests')
    previous_plan = load(Path(previous_repair)/'repair-plan.json') if previous_repair else None
    if previous_plan:
        if previous_plan['source_cohort_sha256'] != plan['source_cohort_sha256']:
            raise ValueError('Previous repair used a different original cohort')
        if load(Path(previous_repair)/'trial/progress.json').get('stage') != 'finished':
            raise ValueError('Previous repair has not finished; do not start a duplicate worker')
    for ident in ids:
        row = rows[ident]; parent = Path(row['input'])
        if live_trial.sha(parent) != row['input_sha256']:
            raise ValueError('Original input changed: '+ident)
        folders = list((source/'shards').glob('*/cases/'+ident))
        if len(folders) != 1:
            raise ValueError('Exactly one original case directory required: '+ident)
        old = folders[0]; original = load(old/'result.json')
        if original['status'] == 'paired_completed':
            raise ValueError('This repair allocation is restricted to incomplete cases')
        baseline = old/'decision/baseline'
        if not (baseline/'response.json').exists():
            raise ValueError('Baseline receipt is required; no replacement A request allowed')
        input_path = root/'inputs'/ident/'bundle.json'
        _copy(parent, input_path)
        inventory = []
        for file in sorted(old.rglob('*.json')):
            inventory.append({'path': str(file.resolve()), 'sha256': live_trial.sha(file)})
        for file in baseline.rglob('*.json'):
            _copy(file, imports/'cases'/ident/'decision/baseline'/file.relative_to(baseline))
        prior_usage = {stage: original[stage+'_usage']['totals']
                       for stage in ('super', 'baseline', 'enriched')}
        seed = None
        if previous_plan:
            previous_case = Path(previous_repair)/'trial/cases'/ident
            previous = load(previous_case/'result.json')
            if 'map_error' not in previous or previous.get('baseline', {}).get('status') != 'completed':
                raise ValueError('Only a completed-baseline, failed-map case can receive a map continuation')
            old_row = next(r for r in previous_plan['cases'] if r['id'] == ident)
            if old_row['input_sha256'] != row['input_sha256']:
                raise ValueError('Previous repair evidence changed')
            for file in sorted(previous_case.rglob('*.json')):
                inventory.append({'path': str(file.resolve()), 'sha256': live_trial.sha(file)})
            for stage in prior_usage:
                base = old_row['original_usage'][stage]
                added = previous[stage+'_usage']['totals']
                prior_usage[stage] = dict(base)
                if added['new_http_attempts']:
                    prior_usage[stage] = {k: base.get(k, 0) + added.get(k, 0)
                        for k in sorted(set(base) | set(added)) if k != 'new_http_attempts'}
            last = sorted((previous_case/'map').glob('message-*.json'))[-1]
            seed = load(last)
            # Do not forward hidden reasoning or free prose from a failed model response.
            seed = {'role': 'assistant', 'tool_calls': seed['tool_calls']}
        plan['cases'].append({'id': ident, 'question': row['question'], 'stream': row['stream'],
            'input': str(input_path.resolve()), 'input_sha256': row['input_sha256'],
            'original_case': str(old.resolve()), 'original_records': inventory,
            'original_usage': prior_usage, 'map_seed': seed})
    path = root/'repair-plan.json'
    _freeze(path, plan)
    _freeze(imports/'identity.json', {'cases': plan['cases'], 'source_cohort_sha256': plan['source_cohort_sha256'],
        'new_http_allowed': False})
    return plan


def _copy(source, target):
    target = Path(target)
    if target.exists():
        if live_trial.sha(source) != live_trial.sha(target):
            raise ValueError('Imported evidence or receipt changed')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _freeze(path, value):
    """Equal JSON identities retain their original byte hash across resumption."""
    path = Path(path)
    if path.exists():
        if load(path) != value:
            raise ValueError('Frozen repair membership, evidence, implementation or allocation changed')
        return
    save(path, value)


def run(source, ids, root, execute=False, previous_repair=None, super_http_cap=2):
    root = Path(root)
    plan = prepare(source, ids, root, previous_repair, super_http_cap)
    result = live_trial.run([r['input'] for r in plan['cases']], root/'trial', execute,
                            root/'baseline-imports',
                            map_seeds={r['id']: r['map_seed'] for r in plan['cases'] if r['map_seed'] is not None},
                            super_http_cap=super_http_cap)
    by_id = {r['id']: r for r in plan['cases']}
    for case in result['cases']:
        original = by_id[case['id']]
        case['original_records_preserved'] = all(live_trial.sha(record['path']) == record['sha256']
                                                 for record in original['original_records'])
        if not case['original_records_preserved']:
            raise ValueError('Original trial record changed')
        for stage in ('super', 'baseline', 'enriched'):
            case[stage+'_cumulative_http_attempts'] = (
                original['original_usage'][stage]['http_attempts'] +
                case[stage+'_usage']['totals']['new_http_attempts'])
        case['original_map_status'] = load(Path(original['original_case'])/'result.json').get('map_error')
    result.update(protocol=plan['protocol'], repair_plan_sha256=live_trial.sha(root/'repair-plan.json'),
        finished_at_utc=now(), comparison_limit=plan['comparison_limit'],
        old_budgets_reset=False, baseline_inference_reused=bool(execute), new_mercury_baseline_http=0)
    save(root/'repair-report.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--ids', nargs='+', required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--previous-repair', type=Path)
    p.add_argument('--super-http-cap', type=int, choices=[1, 2], default=2)
    args = p.parse_args()
    run(args.source, args.ids, args.root, args.execute, args.previous_repair, args.super_http_cap)


if __name__ == '__main__':
    main()
