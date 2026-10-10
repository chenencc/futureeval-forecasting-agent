"""Counterbalanced frozen-body v1/v2 pilot, with one shared direct decision."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.research_loop import decision, live_trial, simple_map, state
from ForecastAgent.runtime.task_lock import task_lock


def freeze(cohort, ids, legacy_worktree, root):
    cohort, legacy_worktree, root = Path(cohort), Path(legacy_worktree), Path(root)
    source = load(cohort); rows = {r['id']: r for r in source['cases']}
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Explicit unique case IDs required')
    plan = {'schema': 'source-first-paired-map-pilot-v1', 'source_cohort_sha256': live_trial.sha(cohort),
        'legacy_worktree': str(legacy_worktree.resolve()), 'new_worktree': str(Path.cwd().resolve()),
        'legacy_implementation': {p.name: live_trial.sha(p) for p in sorted((legacy_worktree/'ForecastAgent/research_loop').glob('*.py'))},
        'new_implementation': decision.implementation_hashes(), 'cases': [],
        'controls': {'original_text': 'identical in all arms', 'super_model': live_trial.SUPER,
            'super_physical_http_per_map': 2, 'super_output_tokens': live_trial.OUTPUT_TOKENS,
            'super_reasoning': live_trial.REASONING, 'mercury_model': 'inception/mercury-decide:free',
            'mercury_physical_http_per_scoring_arm': 1, 'searches': 0, 'fetches': 0,
            'old_budgets_reset': False, 'new_bounded_experiment': True, 'submission': False},
        'selection_warning': 'Five previously audited cases; regression pilot, not an unseen generalization or accuracy test.',
        'quality_warning': 'No verified outcomes loaded. Shared A is reused exactly; v1/v2 order alternates. Single model sample per arm.'}
    for index, ident in enumerate(ids):
        row = rows[ident]; original = Path(row['input'])
        if live_trial.sha(original) != row['input_sha256']:
            raise ValueError('Frozen input changed: '+ident)
        target = root/'inputs'/ident/'bundle.json'
        if target.exists() and live_trial.sha(target) != row['input_sha256']:
            raise ValueError('Copied input changed: '+ident)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
        parents = root/'inputs'/ident/'parents.json'; save(parents, [str(target.resolve())])
        plan['cases'].append({'id': ident, 'question': row['question'], 'question_type': row['question_type'],
            'input': str(target.resolve()), 'original': str(original.resolve()), 'sha256': row['input_sha256'],
            'parents': str(parents.resolve()), 'order': ['legacy', 'source_first'] if index % 2 == 0 else ['source_first', 'legacy']})
    if (root/'plan.json').exists() and load(root/'plan.json') != plan:
        raise ValueError('Frozen pilot identity changed; do not restart budgets')
    save(root/'plan.json', plan)
    return plan


def aggregate(root, plan):
    root = Path(root); cases = []
    for entry in plan['cases']:
        case = {k: entry[k] for k in ('id', 'question', 'question_type', 'order')}
        original_states = []
        for arm in entry['order']:
            folder = root/'cases'/entry['id']/arm
            file = folder/'cases'/entry['id']/'result.json'
            if file.exists():
                result = load(file); case[arm] = result
                original_states.append(load(folder/'cases'/entry['id']/'common-original-state.json'))
            elif (folder/'stage-error.json').exists():
                case[arm] = {'status': 'stage_failed', 'error': load(folder/'stage-error.json')}
            else:
                case[arm] = {'status': 'not_completed'}
        case['equal_original_state'] = len(original_states) == 2 and original_states[0] == original_states[1]
        case['input_preserved'] = all(live_trial.sha(entry[k]) == entry['sha256'] for k in ('input', 'original'))
        cases.append(case)
    complete = [c for c in cases if all(c[a]['status'] == 'paired_completed' for a in ('legacy', 'source_first'))]
    report = {'schema': plan['schema'], 'at_utc': state.now(), 'processed_pairs': len(complete), 'case_count': len(cases),
        'cases': cases, 'submitted': False, 'new_searches': 0, 'new_fetches': 0,
        'accuracy': None, 'brier': None, 'selection_warning': plan['selection_warning'], 'quality_warning': plan['quality_warning']}
    save(root/'report.json', report)
    return report


def run(cohort, ids, legacy_worktree, root, execute=False):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        plan = freeze(cohort, ids, legacy_worktree, root)
        if not execute:
            return aggregate(root, plan)
        for entry in plan['cases']:
            for index, arm in enumerate(entry['order']):
                folder = root/'cases'/entry['id']/arm; folder.mkdir(parents=True, exist_ok=True)
                save(root/'progress.json', {'stage': arm, 'id': entry['id'], 'at_utc': state.now()})
                cmd = [sys.executable, '-X', 'utf8', '-u', '-m', 'ForecastAgent.research_loop.live_trial',
                    '--parents', entry['parents'], '--root', str(folder.resolve()), '--execute']
                if arm == 'source_first':
                    cmd += ['--map-protocol', simple_map.PROTOCOL]
                first = root/'cases'/entry['id']/entry['order'][0]
                if index and (first/'cases'/entry['id']/'decision/baseline/response.json').exists():
                    cmd += ['--baseline-from', str(first.resolve())]
                worktree = plan['legacy_worktree'] if arm == 'legacy' else plan['new_worktree']
                env = dict(os.environ); env['PYTHONPATH'] = worktree
                started = time.monotonic()
                flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                with (folder/'worker.stdout.log').open('a', encoding='utf-8') as out, (folder/'worker.stderr.log').open('a', encoding='utf-8') as err:
                    try:
                        process = subprocess.run(cmd, cwd=worktree, env=env, stdout=out, stderr=err,
                            creationflags=flags, timeout=900)
                        if process.returncode:
                            save(folder/'stage-error.json', {'returncode': process.returncode,
                                'journal_preserved': True, 'stderr': str(folder/'worker.stderr.log')})
                    except subprocess.TimeoutExpired:
                        save(folder/'stage-error.json', {'status': 'bounded_timeout', 'journal_preserved': True,
                            'allowance_reset': False})
                save(folder/'worker.json', {'command': cmd, 'worktree': worktree,
                    'wall_seconds': time.monotonic()-started, 'at_utc': state.now()})
                report = aggregate(root, plan)
                print(json.dumps({'id': entry['id'], 'stage': arm, 'processed_pairs': report['processed_pairs']}), flush=True)
        save(root/'progress.json', {'stage': 'finished', 'at_utc': state.now()})
        return aggregate(root, plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--ids', nargs='+', required=True)
    parser.add_argument('--legacy-worktree', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    run(args.cohort, args.ids, args.legacy_worktree, args.root, args.execute)


if __name__ == '__main__':
    main()
