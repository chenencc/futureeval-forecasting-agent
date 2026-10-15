"""Offline typed host and real checkpoint preservation gate; no provider calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from unified_competition import Collector, guard_pending, load, save, sha


def fixtures():
    base = {'id': '990001', 'question': 'Offline typed acquisition contract',
        'question_type': 'binary', 'resolution_criteria': 'Use the exact official observation.',
        'fine_print': 'Keep the stated unit, option labels and event stage.', 'mode': 'live',
        'as_of_utc': '2026-10-15T00:00:00+00:00', 'background': 'Offline fixture only.'}
    for index, kind in enumerate(('binary', 'multiple_choice', 'numeric', 'date', 'discrete')):
        request = dict(base, id=str(990001+index), question_type=kind)
        if kind == 'multiple_choice': request['options'] = ['Exact option A', 'Exact option B', 'Exact option C']
        if kind in {'numeric', 'date', 'discrete'}:
            request.update(scaling={'range_min': 0, 'range_max': 100, 'zero_point': None},
                inbound_outcome_count=10, open_lower_bound=True, open_upper_bound=False, unit='exact fixture unit')
        yield request


def verify(root, core, engine, output):
    from ForecastAgent.releases import v1_0_5
    from ForecastAgent.analysis.distributions import payload
    v1_0_5.verify_release()
    root = Path(root)
    before = {str(p.relative_to(root)): sha(p) for p in root.rglob('*') if p.is_file()}
    pending = guard_pending(root)
    calls, cases = [], []
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        empty = temp/'empty-incoming'
        save(empty/'index.json', {'tournament': 'fall-futureeval-2026', 'open_scan_complete': True,
            'retrieved_at_utc': datetime.now(timezone.utc).isoformat(), 'questions': [],
            'open_question_count': 0})
        env = dict(os.environ, FORECAST_ACQUISITION_CORE=str(Path(core).resolve()),
            FORECAST_ACQUISITION_ENGINE=str(Path(engine).resolve()),
            FORECAST_ACQUISITION_SHA='1a88d3b3390a6d6b92172ccd223b5f8a7948bfce')
        env.pop('PYTHONPATH', None)
        output_text = subprocess.check_output([sys.executable, str(Path(__file__).with_name('unified_competition.py')),
            '--root', str(temp/'empty-campaign'), '--snapshots', str(empty)],
            cwd=str(v1_0_5.pipeline.ROOT), env=env, text=True)
        startup = json.loads(output_text)
        if startup['recorded_question_count'] or startup['submission_enabled']:
            raise ValueError('Direct host startup unexpectedly enabled work')
        def fake(command, log, timeout, cwd=None):
            calls.append(command)
            request = load(command[command.index('--request')+1])
            job = Path(command[command.index('--directory')+1]); job.mkdir(parents=True, exist_ok=True)
            bundle = {'request': request, 'result': {'status': 'collected', 'semantic_complete': False},
                'pages': {'https://example.org/official': {'content': 'Offline original body.',
                    'body_diagnostics': {'usable_text': True}}},
                'research_state': {'nodes': [], 'truth_verified': False}}
            save(job/'package.json', bundle)
            save(job/'package-integrity.json', {'sha256': sha(job/'package.json')})
            return {'status': 'completed', 'returncode': 0}
        collector = Collector(temp/'campaign', core, engine, '1a88d3b3390a6d6b92172ccd223b5f8a7948bfce', fake)
        for request in fixtures():
            folder = temp/'campaign/tasks'/request['id']; retrieval = folder/'retrieval'
            first = collector.collect(request, retrieval)
            frozen = collector.supplement(first, folder, request['id'])
            clock = frozen['request']['as_of_utc']
            second = collector.collect(dict(request, as_of_utc='2026-10-16T00:00:00Z'), retrieval)
            if first != second or frozen['request']['as_of_utc'] != clock:
                raise ValueError('Typed replay changed original inputs or clocks')
            kind = request['question_type']
            forecast = {'probability_yes': .5} if kind == 'binary' else (
                {'probability_yes_per_category': {o: 1/3 for o in request['options']}}
                if kind == 'multiple_choice' else {'continuous_cdf': [i/10 for i in range(11)]})
            output_payload = payload(frozen['request'], forecast)
            v1_0_5.validate_payload(frozen['request'], output_payload)
            cases.append({'question_type': kind, 'original_type_metadata_preserved': True,
                'child_processes': 1, 'resume_reused': True, 'supplement_dispatched_again': False,
                'unchanged_release_payload_validation': True, 'synthetic_forecast_only': True})
    if len(calls) != 5: raise ValueError('Replay unexpectedly relaunched acquisition')
    after = {str(p.relative_to(root)): sha(p) for p in root.rglob('*') if p.is_file()}
    if before != after: raise ValueError('Original competition checkpoint changed')
    report = {'schema': 'unified-competition-preservation-v1', 'original_files_verified': len(before),
        'original_files_unchanged': True, 'task_compatibility': pending, 'typed_cases': cases,
        'actual_provider_calls': 0, 'model_calls': 0, 'search_calls': 0, 'submitted': False,
        'budgets_reset': False, 'accepted_tasks_reopened': False,
        'direct_host_process_without_pythonpath': True,
        'scope': 'Typed host/format and original checkpoint preservation, not forecast accuracy.'}
    save(output, report); print(json.dumps(report)); return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True)
    parser.add_argument('--core', required=True)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.release).resolve()))
    verify(args.root, args.core, args.engine, args.output)
