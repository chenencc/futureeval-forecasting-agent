"""Fresh 120-case acquisition, deterministic inspection and independent repair."""
import argparse
import os
from pathlib import Path

from ForecastAgent.collection_campaign import prepare, run_batch, read, write, digest
from ForecastAgent.evidence.collection_handoff import prepare as handoff
from ForecastAgent.continuation import plan

FIXTURE = Path(__file__).parent / 'fixtures/recollection_120_v1.json'
AUTHORIZATION = 'User authorized fresh full acquisition of the existing 100 binary and 20 nonbinary questions on 2026-10-03; preserve old experiments and all new lifetime reservations.'


def run(root, *, network=True):
    root = Path(root)
    rows = read(FIXTURE)
    identity = {'protocol': 'recollection-120-v1', 'input_sha256': digest(rows),
                'code_commit': os.environ.get('GITHUB_SHA', 'local'),
                'prior_experiments_preserved': True, 'analysis_or_submission': False}
    if (root / 'experiment.json').exists() and read(root / 'experiment.json') != identity:
        raise ValueError('Frozen experiment changed; refuse a new allowance')
    root.mkdir(parents=True, exist_ok=True)
    write(root / 'experiment.json', identity)
    prepare(root, FIXTURE, 120, raw_recall=True)
    run_batch(root, 5)
    campaign = read(root / 'campaign.json')
    errors = []
    for ident, entry in campaign['tasks'].items():
        if entry['status'] not in {'acquired', 'closed_with_gaps', 'needs_attention', 'blocked_budget'}:
            continue
        folder = root / 'handoffs' / ident
        if (folder / 'analysis-input.json').exists():
            continue
        original = root / 'tasks' / ident / 'bundle.json'
        if not original.exists():
            continue
        try:
            handoff(read(original), folder, network=network)
        except Exception as exc:
            errors.append({'id': ident, 'stage': 'independent_supplement',
                           'error_type': type(exc).__name__, 'detail': str(exc)[:500]})
    write(root / 'full-collection-status.json', {
        'protocol': identity['protocol'], 'expected': 120,
        'acquisition': read(root / 'status.json'),
        'handoffs_completed': len(list((root / 'handoffs').glob('*/analysis-input.json'))),
        'supplement_errors': errors, 'analysis_or_submission_performed': False})
    if errors:
        write(root / 'continuation.json', {'action': 'stop', 'reason': 'supplement_failure',
              'errors': errors, 'budget_reset': False})
    else:
        plan(root, AUTHORIZATION)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    run(args.root)
