"""Linux online repair acceptance using five frozen, mixed-type saved parents."""
import argparse
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.supplement import enhanced

IDS = ['44126', '43658', '45183', '26754', '45045']


def run(parents, journals, output, network=False):
    parents, journals, output = map(Path, (parents, journals, output))
    inputs = {p.parent.name: p for p in parents.glob('checked-snapshot-*/tasks/*/analysis-input.json')}
    bundles = {i: load(p) for i, p in inputs.items() if i in IDS}
    # Fall back to canonical, still-available full-pipeline artifacts, restoring
    # their exact independent repair overlays rather than fabricating budgets.
    from ForecastAgent.analysis.inputs import resolve_bundle
    for path in parents.glob('nonbinary-full-pipeline-*/tasks/*/acquisition/bundle.json'):
        ident = path.parent.parent.name
        if ident in IDS:
            bundles[ident] = resolve_bundle(load(path), ident, path.parent.parent/'supplement')
    prior = {}
    for path in journals.rglob('supplement.json'):
        if path.parent.name not in IDS: continue
        row = load(path)
        if path.parent.name in prior and digest(prior[path.parent.name]) != digest(row):
            raise ValueError('Ambiguous prior journal: '+path.parent.name)
        prior[path.parent.name] = row
    if not set(IDS) <= set(bundles) or not set(IDS) <= set(prior):
        raise ValueError('Frozen input or prior journal missing')
    identity = {'protocol': 'enhanced-online-five-acceptance-v1', 'ids': IDS,
                'input_sha256': {i: digest(bundles[i]) for i in IDS},
                'prior_journal_sha256': {i: digest(prior[i]) for i in IDS},
                'caps': enhanced.CAPS, 'budget_reset': False, 'search_calls_enabled': False,
                'model_calls_enabled': False}
    if (output/'manifest.json').exists() and load(output/'manifest.json') != identity:
        raise ValueError('Frozen pilot changed')
    save(output/'manifest.json', identity); results = []
    for ident in IDS:
        folder = output/'tasks'/ident; original = bundles[ident]
        save(folder/'parent-input.json', original); save(folder/'prior-supplement.json', prior[ident])
        before = enhanced.plan(original)
        try:
            overlay = enhanced.run(original, folder/'candidate', prior=[prior[ident]], network=network)
            state = load(folder/'candidate/state.json') if (folder/'candidate/state.json').exists() else {'attempts': []}
            state_hash = digest(state); view_hash = digest(overlay)
            # Restart acceptance: same frozen inputs must never make new calls.
            replay = enhanced.run(original, folder/'candidate', prior=[prior[ident]], network=network)
            restored = load(folder/'candidate/state.json') if (folder/'candidate/state.json').exists() else {'attempts': []}
            if digest(restored) != state_hash or digest(replay) != view_hash:
                raise ValueError('Resume changed attempts or analysis handoff')
            counters = enhanced.usage(original, [prior[ident]], state)
            inherited = enhanced.usage(original, [prior[ident]], {'attempts': []})
            if any(counters[k] > max(enhanced.CAPS[k], inherited[k]) for k in enhanced.CAPS):
                raise ValueError('Lifetime quota exceeded')
            row = {'id': ident, 'status': 'completed', 'question_type': original['request'].get('question_type', 'binary'),
                   'before_pages': len(original['pages']), 'after_pages': len(overlay['pages']),
                   'before_gaps': before['gaps'], 'remaining_gaps': overlay['enhanced_supplement']['remaining_gaps'],
                   'excluded_pages': len(overlay.get('supplement_excluded_pages', {})),
                   'new_attempts': state['attempts'], 'cumulative_usage': counters,
                   'resume_no_new_calls': True, 'original_unchanged': digest(original) == identity['input_sha256'][ident]}
        except Exception as exc:
            row = {'id': ident, 'status': 'failed', 'error': str(exc), 'preserved_state': str(folder/'candidate')}
        results.append(row); save(output/'report.json', {'rows': results, 'network': network,
            'search_requests': 0, 'model_requests': 0, 'forecast_submissions': 0,
            'runtime_gate_passed': len(results) == 5 and all(r['status'] == 'completed' for r in results),
            'quality_requires_body_review': True})
        print(ident, row['status'], flush=True)
    if any(r['status'] == 'failed' for r in results):
        raise RuntimeError('Pilot defects preserved; do not reset budgets')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('parents', 'journals', 'output'): parser.add_argument('--'+name, required=True)
    parser.add_argument('--network', action='store_true')
    args = parser.parse_args(); run(args.parents, args.journals, args.output, args.network)
