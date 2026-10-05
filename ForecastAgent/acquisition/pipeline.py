"""Collection -> deterministic supplement -> immutable release-compatible package.

This entry point cannot analyze or submit a forecast. Repeated executions resume
the same stage and reservations; completed packages are checksum-verified.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import zipfile
from contextlib import contextmanager
from pathlib import Path

from ForecastAgent.runtime.intelligent_acquisition import CURRENT_STRATEGY, terminal_report
from ForecastAgent.runtime.material_protocol import STRATEGY as V3_STRATEGY
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.supplement.stage import save, digest

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = {'resolution', 'resolved_to', 'assessment', 'probability', 'outcome',
             'freeze_datetime_value', 'community_prediction', 'forecasts'}


def reject_outcomes(value):
    if isinstance(value, dict):
        if set(value) & FORBIDDEN:
            raise ValueError('Outcome labels, previous forecasts and assessments are not acquisition input')
        for child in value.values():
            reject_outcomes(child)
    elif isinstance(value, list):
        for child in value:
            reject_outcomes(child)


def prepare(request):
    reject_outcomes(request)
    if not isinstance(request, dict) or not all(isinstance(request.get(k), str) and request[k].strip() for k in ('question', 'resolution_criteria')):
        raise ValueError('Full question and resolution criteria are required')
    ident = str(request.get('id', ''))
    if not re.fullmatch(r'[0-9]{1,20}', ident):
        raise ValueError('A numeric Metaculus question id is required')
    result = copy.deepcopy(request)
    strategy = request.get('acquisition_strategy', CURRENT_STRATEGY)
    if strategy not in {CURRENT_STRATEGY, V3_STRATEGY}:
        raise ValueError('Use an explicitly supported intelligent acquisition strategy')
    result.update(id=ident, pipeline='collection', acquisition_profile='collection_v3',
                  acquisition_focus='material_recall', acquisition_strategy=strategy)
    result.setdefault('mode', 'live')
    result.setdefault('question_type', 'binary')
    if result['question_type'] not in {'binary', 'multiple_choice', 'numeric', 'date', 'discrete'}:
        raise ValueError('Unsupported question type')
    if result['mode'] != 'live' and not result.get('as_of_utc'):
        raise ValueError('Historical acquisition requires an explicit as_of_utc')
    warnings = [{'field': k, 'issue': 'Not provided; no value was inferred'}
                for k in ('open_time', 'background') if not result.get(k)]
    if result['question_type'] == 'multiple_choice' and not result.get('options'):
        warnings.append({'field': 'options', 'issue': 'Exact platform options were not provided'})
    if result['question_type'] in {'numeric', 'date', 'discrete'}:
        for key in ('scaling', 'inbound_outcome_count'):
            if not result.get(key):
                warnings.append({'field': key, 'issue': 'Platform distribution metadata was not provided'})
    return result, warnings


def verify_baseline():
    manifest = json.loads((ROOT/'ForecastAgent/experiments/acquisition_v2_baseline.json').read_text(encoding='utf-8'))
    for name, expected in manifest['initial_frozen_file_sha256'].items():
        raw = (ROOT/name).read_bytes()
        # Git's text checkout may use CRLF; the manifest hashes repository bytes.
        hashes = {hashlib.sha256(raw).hexdigest(), hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()}
        if expected not in hashes:
            raise ValueError('Frozen release dependency changed: '+name)
    return manifest


def identity(request, supplement_network):
    prepared, warnings = prepare(request)
    base = verify_baseline()
    paths = ['ForecastAgent/runtime/'+name+'.py' for name in (
        'retrieval', 'context', 'guidance', 'collection_actions', 'acquisition', 'needs', 'intelligent_acquisition', 'material_protocol')]
    paths += ['ForecastAgent/acquisition/pipeline.py', 'ForecastAgent/prompts/intelligent_materials.md',
              'ForecastAgent/tools/registry.py', 'ForecastAgent/runtime/contracts.py',
              'ForecastAgent/runtime/tool_selection.py', 'ForecastAgent/supplement/stage.py']
    for prefix in ('ForecastAgent/readers', 'ForecastAgent/evidence', 'ForecastAgent/providers', 'ForecastAgent/skills', 'ForecastAgent/prompts'):
        paths.extend(str(p.relative_to(ROOT)).replace('\\', '/') for p in (ROOT/prefix).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py', '.md'})
    paths = sorted(set(paths))
    return {'schema': 'intelligent-acquisition-pipeline-v1', 'baseline_commit': base['baseline_commit'],
            'input_sha256': digest(request), 'request': prepared, 'request_sha256': digest(prepared),
            'input_warnings': warnings, 'supplement_network': bool(supplement_network),
            'primary_model': os.environ.get('FORECAST_MODEL') or 'nvidia/nemotron-3-ultra-550b-a55b:free',
            'service_fallback_enabled': os.environ.get('FORECAST_MODEL_FALLBACK_SUPER', '1') == '1',
            'candidate_hash_encoding': 'source_bytes_with_lf_line_endings',
            'candidate_sha256': {name: hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for name in paths},
            'budget_profile': base['baseline_limits'], 'analysis_modified': False, 'submission_enabled': False}


@contextmanager
def route_environment():
    key = 'FORECAST_MODEL_FALLBACK_SUPER'
    previous = os.environ.get(key)
    if previous is None:
        os.environ[key] = '1'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)


def resource_report(bundle, collection_root, supplement_root):
    attempts = bundle.get('model_attempts', [])
    known = 0
    unknown = 0
    models = {}
    for attempt in attempts:
        usage = attempt.get('usage')
        if isinstance(usage, dict) and isinstance(usage.get('total_tokens'), int):
            known += usage['total_tokens']
        else:
            unknown += 1
        model = 'not_recorded'
        if attempt.get('path'):
            path = (collection_root/attempt['path']).resolve()
            if not path.is_relative_to(collection_root.resolve()):
                raise ValueError('Model record escaped the task directory')
            record = json.loads(path.read_text(encoding='utf-8'))
            if hashlib.sha256(path.read_bytes()).hexdigest() != attempt['sha256']:
                raise ValueError('Model record hash mismatch')
            model = record.get('request', {}).get('model') or record.get('model') or 'not_recorded'
        models[model] = models.get(model, 0)+1
    supplements = []
    for path in supplement_root.glob('tasks/*/supplement.json'):
        supplements.extend(json.loads(path.read_text(encoding='utf-8')).get('attempts', []))
    return {'model_http_attempts': len(attempts), 'model_attempts_by_backend': models,
            'known_reported_tokens': known, 'unknown_usage_attempts': unknown,
            'tavily_basic_attempts': len(bundle.get('searches', [])),
            'exa_attempts': len(bundle.get('exa_searches', [])),
            'initial_source_http_attempts': len(bundle.get('fetch_attempts', [])),
            'extract_batches': len(bundle.get('extract_attempts', [])),
            'supplement_attempts': supplements}


def run(request, directory, *, supplement_network=False):
    from ForecastAgent.agent import run_research
    from ForecastAgent.runtime.retrieval import RetrievalTask
    from ForecastAgent.supplement.stage import run as supplement, analysis_overlay
    from ForecastAgent.evidence.acceptance import collection_acceptance
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    frozen = identity(request, supplement_network)
    with task_lock(directory):
        ipath = directory/'identity.json'
        if ipath.exists():
            if json.loads(ipath.read_text(encoding='utf-8')) != frozen:
                raise ValueError('Input, code, model policy or network mode changed; refuse a silent restart')
        else:
            save(ipath, frozen)
        state_path = directory/'state.json'
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'stage': 'collection', 'identity_sha256': digest(frozen)}
        if state.get('identity_sha256') != digest(frozen) or state.get('stage') not in {'collection', 'supplement', 'complete'}:
            raise ValueError('Pipeline state identity or stage is invalid')
        if state['stage'] == 'complete':
            package = directory/'package.json'
            if hashlib.sha256(package.read_bytes()).hexdigest() != state['package_sha256']:
                raise ValueError('Completed package hash mismatch')
            if hashlib.sha256((directory/'report.json').read_bytes()).hexdigest() != state['report_sha256']:
                raise ValueError('Completed report hash mismatch')
            return json.loads((directory/'report.json').read_text(encoding='utf-8'))
        task_dir = directory/'collection'
        if state['stage'] == 'collection':
            with route_environment():
                from ForecastAgent.providers.model import reset_route
                reset_route()
                bundle = run_research(frozen['request'], task_dir)
            if not bundle.get('result') or bundle['result'].get('incomplete'):
                state.update(stage='collection', interrupted=True, resumable=bundle.get('result', {}).get('resumable', False))
                save(state_path, state)
                report = {'state': state, 'input_warnings': frozen['input_warnings'],
                          'resources': resource_report(bundle, task_dir, directory/'supplement'),
                          'collection_result': bundle.get('result'), 'analysis_run': False, 'submitted': False}
                save(directory/'report.json', report)
                return report
            raw = (task_dir/'bundle.json').read_bytes()
            archive = directory/'parent.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr('campaign.json', json.dumps({'tasks': {frozen['request']['id']: {'status': 'acquired'}}}))
                z.writestr('tasks/'+frozen['request']['id']+'/bundle.json', raw)
            state.update(stage='supplement', parent_bundle_sha256=hashlib.sha256(raw).hexdigest(),
                         parent_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), interrupted=False)
            save(state_path, state)
        raw = (task_dir/'bundle.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != state['parent_bundle_sha256']:
            raise ValueError('Frozen collection parent changed')
        bundle = json.loads(raw)
        if hashlib.sha256((directory/'parent.zip').read_bytes()).hexdigest() != state['parent_archive_sha256']:
            raise ValueError('Frozen supplement archive hash mismatch')
        with zipfile.ZipFile(directory/'parent.zip') as archive:
            if archive.read('tasks/'+frozen['request']['id']+'/bundle.json') != raw:
                raise ValueError('Supplement archive differs from the frozen collection parent')
        supplement(directory/'parent.zip', directory/'supplement', [frozen['request']['id']], network=supplement_network)
        overlay = analysis_overlay(bundle, directory/'supplement', frozen['request']['id'])
        task = RetrievalTask(task_dir, frozen['request'])
        task.bundle = overlay  # Inspection only; do not save the overlay into the parent ledger.
        materials = terminal_report(task)
        acceptance = collection_acceptance(overlay)
        report = {'schema': 'intelligent-acquisition-report-v1', 'state': 'complete',
                  'input_warnings': frozen['input_warnings'], 'material_report': materials,
                  'capture_integrity': acceptance,
                  'resources': resource_report(bundle, task_dir, directory/'supplement'),
                  'scope': 'Raw material capture and unverified adequacy declarations; no relevance certification or forecast score.',
                  'analysis_run': False, 'submitted': False}
        save(directory/'package.json', overlay)
        save(directory/'report.json', report)
        state.update(stage='complete', package_sha256=hashlib.sha256((directory/'package.json').read_bytes()).hexdigest(),
                     report_sha256=hashlib.sha256((directory/'report.json').read_bytes()).hexdigest())
        save(state_path, state)
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--execute', action='store_true', help='Permit acquisition model/search calls; default is preflight only')
    parser.add_argument('--supplement-network', action='store_true', help='Permit bounded release HTTP/browser repairs')
    args = parser.parse_args()
    request = json.loads(args.input.read_text(encoding='utf-8'))
    result = run(request, args.root, supplement_network=args.supplement_network) if args.execute else identity(request, args.supplement_network)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
