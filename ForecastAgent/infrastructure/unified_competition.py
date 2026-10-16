"""Typed competition host for the same acquisition core used by market workers.

Delivery and analysis execute from the unchanged immutable competition release.
Collection executes in a separate pinned-engine process and has no platform API.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

MODULE_ROOT = Path(__file__).resolve().parent


class AcquisitionBudgetHold(RuntimeError):
    def __init__(self, receipt, execution_id):
        self.receipt, self.execution_id = receipt, execution_id
        super().__init__('Preserved local acquisition budget hold: '+receipt['scope'])


def classify_budget_hold(exc, task, now, original):
    if not isinstance(exc, AcquisitionBudgetHold):
        return original(exc, task, now)
    receipt = exc.receipt
    task['acquisition_budget_hold'] = receipt
    if receipt['scope'] == 'task_lifetime':
        return 'provider_blocked', None
    excluded = task.setdefault('acquisition_budget_wait_executions', [])
    if exc.execution_id not in excluded:
        excluded.append(exc.execution_id)
        task['collection_executions'] = max(0, task.get('collection_executions', 0)-1)
    retry = now + timedelta(minutes=2)
    if receipt.get('retry_at') and receipt['scope'] == 'utc_day':
        retry = max(retry, datetime.fromisoformat(receipt['retry_at'].replace('Z', '+00:00')))
    return 'retry_wait', retry.isoformat()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(tmp, path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Collector:
    def __init__(self, campaign_root, core, engine, expected, runner=None):
        self.root, self.core, self.engine = map(lambda p: Path(p).resolve(), (campaign_root, core, engine))
        self.expected = expected
        self.runner = runner

    def collect(self, request, retrieval):
        retrieval = Path(retrieval).resolve(); retrieval.mkdir(parents=True, exist_ok=True)
        marker = retrieval/'unified-bridge.json'
        if marker.exists():
            self.check(retrieval)
            saved = load(retrieval/'original-contract.json')
            for key, value in request.items():
                if key != 'as_of_utc' and saved.get(key) != value:
                    raise ValueError('Current platform request changed: '+key)
            return load(retrieval/'bundle.json')
        # Started legacy collection retains its own code, reservations and caps.
        # Completed old tasks never enter this callback; adoption is not a reset.
        if not (retrieval/'original-contract.json').exists() and any(retrieval.iterdir()):
            raise ValueError('Legacy acquisition requires its original collector; refuse quota reset')
        contract_path = retrieval/'original-contract.json'
        if contract_path.exists():
            original = load(contract_path)
            for key, value in request.items():
                if key != 'as_of_utc' and original.get(key) != value:
                    raise ValueError('Frozen platform request changed: '+key)
        else:
            original = dict(request)
            original['as_of_utc'] = datetime.now(timezone.utc).isoformat()
            save(contract_path, original)
        job = retrieval/'unified-agent'
        execution_id = uuid.uuid4().hex
        command = [sys.executable, '-u', str(MODULE_ROOT/'unified_acquisition.py'),
            '--core', str(self.core), '--engine', str(self.engine), '--expected-core', self.expected,
            '--request', str(contract_path), '--directory', str(job),
            '--budget-root', str(self.root/'unified-provider'), '--execution-id', execution_id]
        execute = self.runner
        if execute is None:
            from ForecastAgent.releases.guard import execute
        timeout = min(1750, max(1, float(os.environ.get('FORECAST_TASK_PROCESS_DEADLINE_EPOCH',
                      time.time()+1800)) - time.time()-30))
        result = execute(command, retrieval/'unified-process.log', timeout, cwd=str(self.core))
        save(retrieval/'last-process.json', {**result,
            'core_commit': self.expected, 'budgets_reset': False})
        if result['status'] != 'completed' or result['returncode']:
            outcome = job/'execution-result.json'
            receipt = load(outcome) if outcome.exists() else {}
            if receipt.get('execution_id') == execution_id and receipt.get('status') == 'local_budget_hold':
                raise AcquisitionBudgetHold(receipt['receipt'], execution_id)
            raise RuntimeError('Unified acquisition interrupted; original journals and budgets retained')
        package = job/'package.json'; integrity = load(job/'package-integrity.json')
        if sha(package) != integrity['sha256']:
            raise ValueError('Unified package hash mismatch')
        bundle = load(package)
        for field in ('id', 'question_type', 'question', 'resolution_criteria', 'fine_print', 'options',
                      'scaling', 'inbound_outcome_count', 'open_lower_bound', 'open_upper_bound', 'unit'):
            if bundle['request'].get(field) != original.get(field):
                raise ValueError('Unified package changed platform field: '+field)
        if not any(p.get('content') and p.get('body_diagnostics', {}).get('usable_text', True)
                   for p in bundle.get('pages', {}).values()):
            raise RuntimeError('Unified acquisition preserved no readable original; inspect saved gaps')
        # This is a host stage marker, not a replacement for the collector result.
        bridge = {'request': original, 'result': {'status': 'raw_package_exported',
            'incomplete': False, 'raw_export_only': True, 'semantic_complete': False},
            'unified_acquisition': {'source': 'unified-agent/package.json',
                'sha256': sha(package), 'core_commit': self.expected,
                'original_collector_result': bundle.get('result')}}
        save(retrieval/'bundle.json', bridge)
        save(marker, {'bridge_sha256': sha(retrieval/'bundle.json'),
            'contract_sha256': sha(contract_path), 'package_sha256': sha(package),
            'core_commit': self.expected, 'budgets_reset': False})
        return self.check(retrieval)

    def check(self, retrieval):
        retrieval = Path(retrieval).resolve(); marker = load(retrieval/'unified-bridge.json')
        if marker['core_commit'] != self.expected:
            raise ValueError('Frozen unified acquisition core changed')
        for relative, field in [('bundle.json', 'bridge_sha256'),
                ('original-contract.json', 'contract_sha256'), ('unified-agent/package.json', 'package_sha256')]:
            if sha(retrieval/relative) != marker[field]:
                raise ValueError('Frozen unified bridge changed: '+relative)
        return load(retrieval/'bundle.json')

    def supplement(self, adopted, folder, ident):
        retrieval = Path(folder)/'retrieval'
        bridge = self.check(retrieval)
        if str(bridge['request']['id']) != str(ident):
            raise ValueError('Wrong unified package identity')
        for field in ('question', 'resolution_criteria', 'fine_print', 'question_type', 'options',
                      'scaling', 'inbound_outcome_count'):
            if adopted['request'].get(field) != bridge['request'].get(field):
                raise ValueError('Current platform rules disagree with unified evidence: '+field)
        # All supplement/map stages have already run in the shared callable.
        # Keep their actual clocks and do not grant another supplement allowance.
        return load(retrieval/'unified-agent/package.json')


def guard_pending(root):
    root = Path(root)
    if not (root/'campaign.json').exists():
        return {'terminal_preserved': 0, 'pending_compatible': 0}
    from ForecastAgent.competition.recovery_policy import TERMINAL
    state = load(root/'campaign.json'); pending = 0
    for ident, task in state['tasks'].items():
        if task['stage'] in TERMINAL:
            continue
        folder = root/'tasks'/ident
        if (folder/'submission.json').exists() or (folder/'candidate.json').exists():
            continue
        retrieval = folder/'retrieval'
        if retrieval.exists() and any(retrieval.iterdir()) and not (retrieval/'original-contract.json').exists():
            raise ValueError('In-flight legacy collection blocks unification: '+ident)
        pending += 1
    return {'terminal_preserved': sum(t['stage'] in TERMINAL for t in state['tasks'].values()),
            'pending_compatible': pending, 'budget_reset': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--snapshots', required=True)
    parser.add_argument('--submit', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--limit', type=int, default=5)
    args = parser.parse_args()
    # The supervisor starts this absolute script from the frozen release cwd.
    # Python otherwise searches the infrastructure directory, not that checkout.
    sys.path.insert(0, os.getcwd())
    from ForecastAgent.releases import v1_0_5 as release
    from ForecastAgent.releases.guard import execute
    release.pipeline.verify_baseline(); release.verify_release()
    compatibility = guard_pending(args.root)
    core = os.environ['FORECAST_ACQUISITION_CORE']
    engine = os.environ['FORECAST_ACQUISITION_ENGINE']
    expected = os.environ['FORECAST_ACQUISITION_SHA']
    collector = Collector(args.root, core, engine, expected)
    def runner(command, log, timeout, cwd=None):
        old = ['-m', 'ForecastAgent.releases.v1_0_5']
        index = command.index('-m')
        if command[index:index+2] != old:
            raise ValueError('Unexpected release supervisor child')
        routed = command[:index] + [str(Path(__file__).resolve())] + command[index+2:]
        previous = os.environ.get('FORECAST_TASK_PROCESS_DEADLINE_EPOCH')
        os.environ['FORECAST_TASK_PROCESS_DEADLINE_EPOCH'] = str(time.time()+timeout)
        try:
            return execute(routed, log, timeout, cwd=cwd)
        finally:
            if previous is None: os.environ.pop('FORECAST_TASK_PROCESS_DEADLINE_EPOCH', None)
            else: os.environ['FORECAST_TASK_PROCESS_DEADLINE_EPOCH'] = previous
    original_classify = release.classify
    from competition_context import wrap_analyze, reconcile
    context_recovery = reconcile(args.root)
    with patch.object(release.context_decisions, 'analyze', wrap_analyze(release.context_decisions.analyze)), patch.object(release, 'supplement', collector.supplement), patch.object(release, 'classify',
            lambda exc, task, now: classify_budget_hold(exc, task, now, original_classify)):
        if args.once:
            report = release.once(args.root, args.snapshots, enabled=args.submit,
                collector=collector.collect, limit=1)
        else:
            report = release.supervise(args.root, args.snapshots, submit=args.submit,
                limit=args.limit, task_seconds=1800, batch_seconds=6600, process_runner=runner)
    report['unified_acquisition'] = {'core_commit': expected, 'compatibility': compatibility,
        'collector': 'ForecastAgent.market_repricing.worker.acquire',
        'analysis_and_delivery': 'v1.0.5-delivery.1 with official-decision-envelope-v1',
        'context_recovery': context_recovery, 'budgets_reset': False}
    save(Path(args.root)/'unified-worker-report.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
