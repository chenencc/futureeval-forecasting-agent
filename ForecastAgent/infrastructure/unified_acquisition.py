"""Run both hosts through the immutable prediction-market acquisition callable.

This launcher executes in its own interpreter. It never loads competition
delivery code into the research engine and never exposes a submission method.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import urllib.request

VERSION = 'market-unified-acquisition-v1'
KINDS = {'binary', 'multiple_choice', 'numeric', 'date', 'discrete'}
CORE_FILES = ('worker.py', 'runtime.py', 'evidence_capture.py', 'materials.py',
              'source_profile.py', 'search_policy.py', 'context_delivery.py',
              'budget_runtime.py', 'budgets.py', 'policy.json')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(temp, path)


def freeze(path, value):
    path = Path(path)
    if path.exists():
        if load(path) != value:
            raise ValueError('Frozen unified acquisition identity changed: '+path.name)
    else:
        save(path, value)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def commit(root):
    root = Path(root).resolve()
    result = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                     encoding='utf-8').strip()
    subprocess.run(['git', '-C', str(root), 'diff', '--quiet', 'HEAD'], check=True)
    return result


def verify(core, engine=None, expected=None):
    core = Path(core).resolve()
    expected = expected or os.environ.get('FORECAST_ACQUISITION_SHA')
    if not expected or len(expected) != 40 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('An immutable FORECAST_ACQUISITION_SHA is required')
    if commit(core) != expected:
        raise ValueError('Selected acquisition core does not match the shared pin')
    sys.path.insert(0, str(core))
    from ForecastAgent.market_repricing import ENGINE_SHA, SUPER
    if engine is not None and commit(engine) != ENGINE_SHA:
        raise ValueError('Selected acquisition engine does not match the shared core')
    from ForecastAgent.market_repricing.deployment import budget_compatibility
    budget_compatibility()
    folder = core / 'ForecastAgent/market_repricing'
    return {'version': VERSION, 'core_commit': expected, 'engine_commit': ENGINE_SHA,
        'collector': 'ForecastAgent.market_repricing.worker.acquire',
        'repair': 'ForecastAgent.market_repricing.worker.repair_materials',
        'collection_model': SUPER,
        'files_sha256_lf': {name: hashlib.sha256((folder/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                            for name in CORE_FILES},
        'search': {'basic': 2, 'advanced': 1, 'total_tavily': 3, 'exa': 1},
        'initial_fetch': 8, 'eager_per_search': 4,
        'market_supplement_http': 4, 'market_supplement_browser': 2,
        'research_map': True, 'channel_tools': True, 'recover_data': True,
        'budgets_reset': False}


def check_request(request):
    from ForecastAgent.acquisition.pipeline import reject_outcomes
    reject_outcomes(request)
    if not isinstance(request, dict) or request.get('question_type') not in KINDS:
        raise ValueError('Unsupported unified acquisition question type')
    for field in ('id', 'question', 'resolution_criteria', 'as_of_utc'):
        if not isinstance(request.get(field), str) or not request[field].strip():
            raise ValueError('Missing original question field: '+field)
    if not request['id'].isdigit() or request.get('mode') != 'live':
        raise ValueError('Unified production acquisition requires a live numeric question identity')
    stamp = datetime.fromisoformat(request['as_of_utc'].replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('An explicit original observation timezone is required')
    if request['question_type'] == 'multiple_choice' and not request.get('options'):
        raise ValueError('Exact platform options are required')
    if request['question_type'] in {'numeric', 'date', 'discrete'}:
        if not request.get('scaling') or not request.get('inbound_outcome_count'):
            raise ValueError('Exact platform scale and CDF metadata are required')


def import_prior_usage(ledger, budget_root):
    """Keep older host transmissions charged when adopting the new shared guard."""
    parent = Path(budget_root).parent
    known = ledger.setdefault('legacy_transport_ids', [])
    for root in (parent/'provider-transport', parent/'exa-transport'):
        for path in sorted(root.rglob('attempts/*.json')):
            ident = path.relative_to(parent).as_posix()
            if ident in known:
                continue
            record = load(path)
            stamp = record.get('started_at_utc')
            provider = record.get('provider') or ('exa' if root.name == 'exa-transport' else None)
            if provider not in {'openrouter', 'tavily', 'exa'} or not stamp:
                raise ValueError('Unrecognized previous physical provider receipt')
            date = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(timezone.utc).date().isoformat()
            ledger.setdefault('provider_reservations', []).append({
                'key': 'legacy:'+digest(ident), 'kind': 'model' if provider == 'openrouter' else 'search',
                'endpoint': record.get('endpoint'), 'day': date, 'run_id': 'legacy:'+digest(ident),
                'reserved_at': stamp, 'status': 'legacy_transmission_reserved',
                'source_path': ident, 'source_sha256': sha(path), 'budget_reset': False})
            known.append(ident)


@contextmanager
def physical_transport(root, ledger, key, cfg):
    """Use the market guard; host backup attempts each re-enter physical counting."""
    from ForecastAgent.market_repricing.worker import provider_budget
    current = urllib.request.urlopen
    raw = current
    while (getattr(raw, '_forecast_provider_transport', False) or
           getattr(raw, '_forecast_exa_transport', False)) and hasattr(raw, 'original'):
        raw = raw.original
    urllib.request.urlopen = raw
    bootstrapped = []
    for module in list(sys.modules.values()):
        if module and getattr(module, '__name__', '').startswith('ForecastAgent') and getattr(module, 'urlopen', None) is current:
            bootstrapped.append(module); module.urlopen = raw
    try:
        with provider_budget(root, ledger, key, cfg) as denials:
            counted = urllib.request.urlopen
            core = Path(importlib.import_module('ForecastAgent.market_repricing').__file__).resolve().parents[1]
            provider = runpy.run_path(str(core/'infrastructure/provider_failover.py'))['ProviderTransport']
            exa = runpy.run_path(str(core/'infrastructure/exa_failover.py'))['ExaTransport']
            routed = provider(counted, {'openrouter': (os.environ.get('OPENROUTER_API_KEY', ''),
                os.environ.get('OPENROUTER_API_KEY2', ''))}, Path(root)/'credentials')
            if os.environ.get('EXA_API_KEY'):
                routed = exa(routed, os.environ['EXA_API_KEY'], os.environ.get('EXA_API_KEY2', ''),
                             Path(root)/'credentials-exa')
            replaced = []
            for module in list(sys.modules.values()):
                if module and getattr(module, '__name__', '').startswith('ForecastAgent') and getattr(module, 'urlopen', None) is counted:
                    replaced.append(module); module.urlopen = routed
            urllib.request.urlopen = routed
            try:
                yield denials
            except Exception:
                # Generic credential wrappers can sanitize the native exception.
                # Retain the market guard's exact non-transmitted denial instead.
                if denials:
                    from ForecastAgent.market_repricing.budgets import ProviderCap
                    raise ProviderCap(denials[-1]) from None
                raise
            finally:
                urllib.request.urlopen = counted
                for module in list(sys.modules.values()):
                    if module and getattr(module, '__name__', '').startswith('ForecastAgent') and getattr(module, 'urlopen', None) is routed:
                        module.urlopen = counted
    finally:
        urllib.request.urlopen = current
        for module in bootstrapped:
            module.urlopen = current


def acquire(request, directory, budget_root, identity, collector=None):
    """The platform supplies a typed contract; the market callable supplies all stages."""
    import copy
    from ForecastAgent.intelligence.research_map import enable
    from ForecastAgent.market_repricing.common import policy
    from ForecastAgent.market_repricing.search_policy import identity as search_identity
    from ForecastAgent.market_repricing.evidence_capture import VERSION as capture_version, policy_identity
    from ForecastAgent.market_repricing.worker import acquire as market_acquire
    from ForecastAgent.market_repricing.budgets import ProviderCap
    from ForecastAgent.runtime.task_lock import task_lock
    check_request(request)
    directory, budget_root = Path(directory).resolve(), Path(budget_root).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    freeze(directory/'original-contract.json', request)
    freeze(directory/'shared-identity.json', identity)
    prepared = enable(copy.deepcopy(request), predictive_focus=True, scoring_delivery=True)
    prepared.update(collection_model=identity['collection_model'],
        market_search_profile=search_identity(), market_acquisition_policy=capture_version,
        market_capture_identity=policy_identity(), acquisition_focus='raw_recall')
    # acquire() reads this exact original typed contract rather than request_for(),
    # whose binary compatibility ID belongs only to venue admission.
    req_path = directory/'collection-request.json'
    if not req_path.exists():
        save(req_path, prepared)
    else:
        saved = load(req_path)
        for field, value in request.items():
            if saved.get(field) != value:
                raise ValueError('Frozen original contract changed: '+field)
    task_path = directory/'host-task.json'
    task = load(task_path) if task_path.exists() else {'key': 'competition:'+request['id'],
        'enrolled_at': request['as_of_utc'], 'acquisition_policy': capture_version,
        'market_search_profile': search_identity()}
    cfg = policy()
    budget_root.mkdir(parents=True, exist_ok=True)
    with task_lock(budget_root):
        state_path = budget_root/'state.json'
        state = load(state_path) if state_path.exists() else {
            'schema': VERSION, 'provider_reservations': [], 'legacy_transport_ids': []}
        if state.get('schema') != VERSION:
            raise ValueError('Unknown unified provider ledger; refusing reset')
        import_prior_usage(state, budget_root)
        save(state_path, state)
        try:
            with physical_transport(budget_root, state, task['key'], cfg) as denials:
                bundle = (collector or market_acquire)(task, directory)
            if denials:
                raise ProviderCap(denials[-1])
            for field in ('id', 'question_type', 'question', 'resolution_criteria', 'fine_print',
                          'options', 'scaling', 'inbound_outcome_count', 'open_lower_bound', 'open_upper_bound', 'unit'):
                if bundle['request'].get(field) != request.get(field):
                    raise ValueError('Market acquisition changed platform contract: '+field)
            freeze(directory/'package.json', bundle)
            save(directory/'package-integrity.json', {'sha256': sha(directory/'package.json'),
                'core': identity, 'contract_sha256': digest(request), 'budget_reset': False})
            return bundle
        finally:
            save(task_path, task)
            save(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True)
    parser.add_argument('--engine')
    parser.add_argument('--expected-core')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--request')
    parser.add_argument('--directory')
    parser.add_argument('--budget-root')
    parser.add_argument('--output')
    parser.add_argument('--execution-id')
    args = parser.parse_args()
    ident = verify(args.core, args.engine, args.expected_core)
    if args.verify_only:
        if args.output: save(args.output, ident)
        print(json.dumps(ident)); return
    if not all((args.engine, args.request, args.directory, args.budget_root)):
        parser.error('Acquisition requires engine, request, directory and budget-root')
    from ForecastAgent.market_repricing.runtime import activate
    activate(args.engine)
    os.environ['MARKET_PIPELINE_SHA'] = ident['core_commit']
    request = load(args.request)
    from ForecastAgent.market_repricing.budgets import ProviderCap
    try:
        acquire(request, args.directory, args.budget_root, ident)
    except ProviderCap as exc:
        save(Path(args.directory)/'execution-result.json', {'execution_id': args.execution_id,
            'status': 'local_budget_hold', 'receipt': exc.receipt, 'budgets_reset': False})
        raise
    print(json.dumps({'status': 'package_saved', 'core': ident,
        'package_sha256': sha(Path(args.directory)/'package.json'),
        'forecast_submitted': False, 'trades': False}))


if __name__ == '__main__':
    main()
