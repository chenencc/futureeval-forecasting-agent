"""Run selected new cases with current information and preserved lifetime budgets."""
import argparse
import json
import os
from pathlib import Path

from ForecastAgent.historical_batch import initialize, validate, write, now
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.runtime.temporal_policy import amend_current


def prepare(root, fixture):
    if not all(os.environ.get(key) for key in ('TAVILY_API_KEY', 'OPENROUTER_API_KEY', 'EXA_API_KEY')):
        raise ValueError('Configure all three provider credentials before creating task budgets')
    batch = initialize(root, fixture, 'collection_v3')
    with task_lock(root):
        rows = validate(root, batch)
        for ident, row in rows.items():
            directory = root / 'tasks' / ident
            if not (directory / 'bundle.json').exists() and batch['tasks'][ident]['attempts']:
                raise ValueError('Consumed task state is missing; refusing a fresh budget')
            directory.mkdir(parents=True, exist_ok=True)
            request = {**row, 'mode': batch['mode'], 'pipeline': 'collection', 'acquisition_profile': 'collection_v3'}
            with task_lock(directory):
                task = RetrievalTask(directory, request)
                amend_current(task.bundle, 'Authorized new five-case current-information pilot; original dates are provenance only')
                task.save()
        provenance = root / 'experiment.json'
        if not provenance.exists():
            write(provenance, {'schema': 'current_information_pilot_v1', 'engine_baseline_commit': 'e4fd1fb',
                'initial_commit': os.environ.get('GITHUB_SHA'), 'fixture': str(fixture),
                'collection_temporal_policy': 'current_information', 'forecast_submissions': False,
                'authorization': 'Operator requested five new questions using the current version',
                'comparison_caveat': 'Different cases: exploratory quality review, not a controlled A/B or historical backtest.'})


def run_one(root, ident):
    with task_lock(root):
        batch = json.loads((root / 'batch.json').read_text(encoding='utf-8'))
        validate(root, batch)
        if ident not in batch['tasks']:
            raise ValueError('Question is not part of the frozen pilot')
        entry = batch['tasks'][ident]
        directory = root / 'tasks' / ident
        bundle = json.loads((directory / 'bundle.json').read_text(encoding='utf-8'))
        if bundle.get('result') and not bundle['result'].get('incomplete'):
            print(json.dumps({'question_id': ident, 'status': 'closed_preserved'}))
            return
        if len(entry['attempts']) >= 5 or len(bundle.get('model_attempts', [])) >= 72:
            raise ValueError('Existing lifetime execution budget exhausted')
        attempt = {'started_at_utc': now().isoformat(), 'status': 'reserved', 'code_commit': os.environ.get('GITHUB_SHA')}
        entry['attempts'].append(attempt)
        entry['status'] = 'running'
        write(root / 'batch.json', batch)
        try:
            output = run_retrieval(bundle['request'], directory, os.environ['TAVILY_API_KEY'], os.environ['OPENROUTER_API_KEY'])
            result = output.get('result') or {}
            entry['status'] = 'incomplete' if not result or result.get('incomplete') else 'complete'
            entry['collection_status'] = result.get('status')
            attempt['status'] = entry['status']
            print(json.dumps({'question_id': ident, 'status': entry['status'], 'result': result}), flush=True)
        except Exception as exc:
            entry['status'] = attempt['status'] = 'failed'
            attempt['error'] = type(exc).__name__
            raise
        finally:
            attempt['finished_at_utc'] = now().isoformat()
            write(root / 'batch.json', batch)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--question')
    args = parser.parse_args()
    if args.action == 'prepare':
        if not args.fixture:
            parser.error('--fixture is required')
        prepare(args.root, args.fixture)
    else:
        if not args.question:
            parser.error('--question is required')
        run_one(args.root, args.question)


if __name__ == '__main__':
    main()
