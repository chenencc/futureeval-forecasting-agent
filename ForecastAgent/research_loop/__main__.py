"""Offline replay first; acquisition and one-attempt scoring require --execute."""
import argparse
import copy
import hashlib
import os
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.research_loop import POLICY, VERSION
from ForecastAgent.research_loop.state import initialize, catalog, audit
from ForecastAgent.research_loop.acceptance import accept

SUPER = 'nvidia/nemotron-3-super-120b-a12b:free'


def replay(bundle_path, proposal_path, root):
    """Create a child artifact; never overwrite the saved parent or its budgets."""
    root = Path(root)
    raw = Path(bundle_path).read_bytes()
    bundle = load(bundle_path)
    from ForecastAgent.acquisition.pipeline import reject_outcomes
    reject_outcomes(bundle['request'])
    parent = hashlib.sha256(raw).hexdigest()
    proposal = load(proposal_path) if proposal_path else None
    from ForecastAgent.research_loop.decision import implementation_hashes
    identity = {'schema': VERSION, 'parent_path': str(Path(bundle_path).resolve()),
        'parent_sha256': parent, 'proposal_sha256': digest(proposal), 'provider_attempts': 0,
        'implementation_sha256': implementation_hashes(),
        'evaluation_mode': 'offline_contract_replay', 'submission_enabled': False}
    from ForecastAgent.runtime.task_lock import task_lock
    root.mkdir(parents=True, exist_ok=True)
    with task_lock(root):
        if (root/'replay-identity.json').exists() and load(root/'replay-identity.json') != identity:
            raise ValueError('Replay parent or proposal changed')
        save(root/'replay-identity.json', identity)
        if (root/'report.json').exists():
            report = load(root/'report.json')
            for name, field in (('research-package.json', 'child_sha256'), ('research-state.json', 'state_sha256')):
                if hashlib.sha256((root/name).read_bytes()).hexdigest() != report.get(field):
                    raise ValueError('Completed replay child checksum mismatch')
            child = load(root/'research-package.json')
            return {'status': 'replayed', 'cached': True, 'provider_attempts': 0, 'map_audit': audit(child)}
        child = copy.deepcopy(bundle)
        child['request']['research_state_policy'] = POLICY
        child['pipeline'] = 'collection'
        initialize(child)
        if proposal:
            accept(child, proposal)
        report = audit(child)
        if child.get('pages') != bundle.get('pages'):
            raise ValueError('Replay changed original material')
        save(root/'research-package.json', child)
        save(root/'research-state.json', child['research_loop'])
        save(root/'report.json', {**identity, 'map_audit': report,
            'child_sha256': hashlib.sha256((root/'research-package.json').read_bytes()).hexdigest(),
            'state_sha256': hashlib.sha256((root/'research-state.json').read_bytes()).hexdigest(),
            'original_sources_preserved': True, 'externally_supplied_proposal': bool(proposal),
            'meaning_or_prediction_quality_tested': False})
        if Path(bundle_path).read_bytes() != raw:
            raise ValueError('Parent source changed during replay')
        return {'status': 'replayed', 'provider_attempts': 0, 'map_audit': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--bundle', type=Path, required=True)
    inspect.add_argument('--query', default='')
    inspect.add_argument('--offset', type=int, default=0)
    inspect.add_argument('--limit', type=int, default=3)
    replay_parser = sub.add_parser('replay')
    replay_parser.add_argument('--bundle', type=Path, required=True)
    replay_parser.add_argument('--proposal', type=Path)
    replay_parser.add_argument('--root', type=Path, required=True)
    collect = sub.add_parser('collect')
    collect.add_argument('--input', type=Path, required=True)
    collect.add_argument('--root', type=Path, required=True)
    collect.add_argument('--execute', action='store_true')
    collect.add_argument('--model', default=SUPER, help='Explicit acquisition model; the input identity freezes it')
    collect.add_argument('--supplement-network', action='store_true')
    decision = sub.add_parser('decide')
    decision.add_argument('--bundle', type=Path, required=True)
    decision.add_argument('--root', type=Path, required=True)
    decision.add_argument('--execute', action='store_true')
    decision.add_argument('--arm', choices=('baseline', 'enriched'), default='enriched')
    args = parser.parse_args()
    if args.command == 'inspect':
        if args.offset < 0 or not 1 <= args.limit <= 4:
            parser.error('Use offset >= 0 and limit 1..4')
        material = catalog(load(args.bundle))
        matches = [s for s in material['spans'].values()
                   if not args.query or args.query.casefold() in s['text'].casefold()]
        result = {'material_sha256': material['material_sha256'],
            'evidence': matches[args.offset:args.offset+args.limit], 'total_matches': len(matches)}
    elif args.command == 'replay':
        result = replay(args.bundle, args.proposal, args.root)
    elif args.command == 'decide':
        from ForecastAgent.research_loop.decision import run
        result = run(load(args.bundle), args.root, execute=args.execute, arm=args.arm)
    else:
        from ForecastAgent.acquisition.pipeline import identity, run
        from ForecastAgent.runtime.material_protocol import STRATEGY
        os.environ['FORECAST_MODEL'] = args.model
        os.environ['FORECAST_MODEL_FALLBACK_SUPER'] = '0'
        request = load(args.input)
        request.update(research_state_policy=POLICY, acquisition_strategy=STRATEGY,
                       source_reading_policy='crawl4ai_v1')
        result = run(request, args.root, supplement_network=args.supplement_network) if args.execute else identity(request, args.supplement_network)
        if args.execute:
            parent = args.root/'package.json'
            if parent.exists():
                child = load(parent)
                # Supplement may add new bodies: stale maps never become current silently.
                child['research_loop'] = load(args.root/'collection/bundle.json').get('research_loop')
                save(args.root/'research-package.json', child)
                save(args.root/'research-state.json', {'ledger': child['research_loop'], 'audit': audit(child),
                    'parent_package_sha256': hashlib.sha256(parent.read_bytes()).hexdigest()})
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
