"""Audited analysis envelopes over frozen original competition inputs.

Internal acquisition controls are not forecasting evidence. Original inputs,
requests, provider reservations and submitted candidates remain immutable.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path

from scoring_boundary import project_bundle, LOCAL_METADATA_FIELDS
ERROR = 'ValueError: Existing state exceeds decision byte bound'
POLICY = 'official-decision-envelope-v2-market'


def project(bundle):
    return project_bundle(bundle)


def wrap_analyze(original):
    def analyze(source, folder, ident):
        from ForecastAgent.analysis.pilot import load, save, digest
        from ForecastAgent.competition.mercury import packet_for
        folder = Path(folder)
        bundle = load(source)
        prior = folder/'official-decision-envelope-v1'
        if (prior/'input.json').exists():
            # Already frozen v1 work keeps its original input and request budget.
            receipt = load(prior/'projection.json')
            if receipt['original_input_sha256'] != digest(bundle):
                raise ValueError('Prior decision envelope original identity changed')
            return original(prior/'input.json', prior, ident)
        projected = project(bundle)
        if projected['request'] == bundle['request']:
            return original(source, folder, ident)
        old = folder/'mercury-v1.0.1'
        # Any frozen request or HTTP reservation owns its existing identity.
        # Do not create another two-attempt allowance for partially used work.
        if any(old.glob('*/request.json')) or any(old.glob('*/http/*.json')) or (folder/'candidate.json').exists():
            return original(source, folder, ident)
        if (old/'identity.json').exists():
            if load(old/'identity.json')['packet_sha256'] != digest(packet_for(bundle)):
                raise ValueError('Frozen original analysis packet identity mismatch')
            if load(old/'packet.json') != packet_for(bundle):
                raise ValueError('Frozen original analysis packet changed')
        target = folder/POLICY
        receipt = {'policy': POLICY, 'original_input_sha256': digest(bundle),
            'projected_input_sha256': digest(projected),
            'retained_question_fields': sorted(projected['request']),
            'omitted_internal_fields': sorted(set(bundle['request']) & set(LOCAL_METADATA_FIELDS)),
            'original_materials_unchanged': projected.get('pages') == bundle.get('pages'),
            'previous_model_attempts': 0, 'budgets_reset': False}
        path = target/'input.json'
        if path.exists() and load(path) != projected:
            raise ValueError('Frozen analysis envelope identity changed')
        if (target/'projection.json').exists() and load(target/'projection.json') != receipt:
            raise ValueError('Frozen analysis envelope receipt changed')
        save(path, projected)
        save(target/'projection.json', receipt)
        # One stable revision directory: retrying never resets its journals.
        return original(path, target, ident)
    return analyze


def reconcile(root, now=None):
    """Reclassify only this diagnosed defect; never silently reopen delivery."""
    from ForecastAgent.analysis.pilot import load, save
    root = Path(root)
    if not (root/'campaign.json').exists():
        return []
    now = now or datetime.now(timezone.utc)
    state = load(root/'campaign.json')
    receipts = []
    for task in state.get('tasks', {}).values():
        if task.get('stage') not in {'needs_review', 'blocked_integrity', 'recovery_wait', 'retry_wait'} or task.get('last_error') != ERROR:
            continue
        folder = root/'tasks'/task['id']
        if (folder/'submission.json').exists() or (folder/'candidate.json').exists():
            continue
        deadline = task.get('deadline_utc')
        expired = deadline and datetime.fromisoformat(deadline.replace('Z', '+00:00')) <= now
        if not expired:
            if task.get('context_envelope_recovery'):
                continue
            core = folder/'analysis-core-1.0.1'/'mercury-v1.0.1'
            if not (folder/'analysis-input.json').exists() or any(core.glob('*/request.json')) or any(core.glob('*/http/*.json')):
                continue
        receipt = {'policy': POLICY, 'at_utc': now.isoformat(), 'previous_stage': task['stage'],
            'previous_error': task['last_error'], 'stage': 'deadline_missed' if expired else 'recovery_wait',
            'new_provider_calls': 0, 'budgets_reset': False}
        task.update(stage=receipt['stage'], retry_at_utc=None, context_envelope_recovery=receipt)
        task.setdefault('history', []).append(receipt)
        receipts.append({'id': task['id'], **receipt})
    if receipts:
        save(root/'campaign.json', state)
    return receipts


if __name__ == '__main__':
    import argparse
    import json
    import os
    import sys
    sys.path.insert(0, os.getcwd())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    print(json.dumps({'policy': POLICY, 'reconciled': reconcile(args.root),
        'provider_calls': 0, 'submitted': False, 'budgets_reset': False}))
