"""Offline paired audit; preserve generations and expose post-generation gates."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ForecastAgent.market_pulse import compact_trial as trial, analysis as base
from ForecastAgent.market_pulse.financial_recovery import ledger_hashes
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.releases.v1_0_5 import verify_release, validate_payload


def audit(root, output):
    if output.exists():
        raise ValueError('Never replace a completed independent audit')
    frozen = ledger_hashes(root)
    manifest = load(root / 'manifest.json'); report = load(root / 'report.json')
    inputs = manifest['inputs']; trial.prior.source_files_unchanged(inputs)
    for path, hashes in manifest['parent_hashes'].items():
        if ledger_hashes(Path(path)) != hashes:
            raise ValueError('Parent provider or source archive changed')
    baseline = load(manifest['baseline'])
    if base.sha(manifest['baseline']) != manifest['baseline_sha256']:
        raise ValueError('Frozen comparison report changed')
    rows = []; previous_stage_receipts = []
    parents = [Path(p) for p in manifest['parent_hashes']]
    for item in inputs:
        folder = root / 'tasks' / item['id']
        original = load(folder / 'result.json')
        bundle, variables, template, state, old = trial.prepare(item, baseline)
        coverage = trial.paired_coverage(item, state, parents)
        current_request = load(folder / 'mercury/request.json')
        if current_request['state'] != state or current_request['questions'] != trial.outcome_questions(
                trial.distribution_spec(bundle['request'])):
            raise ValueError('Independent outcome exposure changed')
        candidate = original['payload']; validate_payload(bundle['request'], candidate)
        projection = None; rejection = None
        if (folder / 'super/raw.json').exists():
            try:
                projection = trial.evaluate_assumptions(load(folder / 'super/raw.json'),
                    template, variables, state['allowed_variable_ids'])
            except Exception as exc:
                rejection = {'type': type(exc).__name__, 'error': str(exc)}
        uncertainty = trial.uncertainty_audit(bundle['request'], candidate, template)
        old_uncertainty = trial.uncertainty_audit(bundle['request'], old['payload'], template)
        baseline_request = Path(coverage['previous_request'])
        previous_stage_receipts.extend(load(p) for p in (baseline_request.parent / 'http').glob('*.json'))
        # The final prior derivation is retained even when its output was rejected.
        # This is a terminal-stage cost comparison, not a from-scratch benchmark.
        selected = None
        for parent in parents:
            for stage in ('super-correction', 'template-super'):
                path = parent / 'tasks' / item['id'] / stage / 'http'
                if path.exists():
                    selected = path
        if selected:
            previous_stage_receipts.extend(load(p) for p in selected.glob('*.json'))
        anchor = projection['central_value'] if projection else original['program_baseline']['central_value']
        median = original['quantiles']['0.5']['value']
        rows.append({'id': item['id'], 'issuer': original['issuer'], 'template': template['method'],
            'original_execution': {'model_projection_accepted': original['model_projection_accepted'],
                'analyst_error': original['analyst_error']},
            'offline_replay': {'model_projection_accepted': projection is not None,
                'rejection': rejection, 'model_projection': projection,
                'policy_changed_after_generation': original['model_projection_accepted'] != (projection is not None)},
            'program_baseline': original['program_baseline'],
            'independent_quantiles': original['quantiles'], 'previous_quantiles': old['quantiles'],
            'uncertainty': uncertainty, 'previous_uncertainty': old_uncertainty,
            'projection_or_baseline_vs_independent_median': {
                'reference_value': anchor, 'independent_median': median,
                'signed_relative_difference': (median - anchor) / abs(anchor) if median is not None and anchor else None,
                'not_averaged': True},
            'paired_coverage': coverage, 'payload_sha256': digest(candidate),
            'cdf_format_valid': True, 'source_semantics_are_cached_diagnostics': True,
            'forecast_accuracy_not_evaluated': True, 'delivery_ready': False, 'submitted': False})
    journals = [load(p) for p in root.glob('tasks/*/**/http/*.json')]
    transport = [load(p) for p in root.glob('provider-transport/openrouter/attempts/*.json')]
    usage = base.usage_audit(journals); previous_usage = base.usage_audit(previous_stage_receipts)
    if len(journals) != 6 or len(transport) > 2 * len(journals):
        raise ValueError('Four-task additive request bound differs')
    if len(previous_stage_receipts) != 8:
        raise ValueError('Prior final-stage comparison must retain eight actual attempts')
    models = Counter(r['request']['model'] for r in journals)
    if models != Counter({trial.prior.finance.MODEL: 2, 'inception/mercury-decide:free': 4}):
        raise ValueError('Unexpected model routing or stage counts')
    for path in root.glob('tasks/*/*/http'):
        if len(list(path.glob('*.json'))) > 1:
            raise ValueError('Per-stage reservation limit exceeded')
    executed_policy = root / 'executed-compact_trial.py'
    result = {'protocol': 'financial-compact-independent-audit-v1',
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'questions': rows,
        'valid_distributions': len(rows), 'valid_program_templates': report['valid_program_templates'],
        'original_execution_accepted_model_projections': report['accepted_model_projections'],
        'offline_replay_accepted_model_projections': sum(r['offline_replay']['model_projection_accepted'] for r in rows),
        'actual_requests': len(transport), 'logical_requests': len(journals),
        'models': dict(models), 'transport_statuses': dict(Counter(str(r.get('http_status')) for r in transport)),
        'credential_roles': dict(Counter(r['credential_role'] for r in transport)),
        'usage': usage, 'previous_final_stage_requests': len(previous_stage_receipts),
        'previous_final_stage_usage': previous_usage,
        'reported_token_change_vs_previous_final_stages': usage['known_tokens'] / previous_usage['known_tokens'] - 1,
        'previous_entire_review_usage': baseline['usage'],
        'previous_entire_review_requests': baseline['total_review_and_correction_actual_requests'],
        'source_review_reused_not_free_for_new_questions': True,
        'post_generation_policy_audit': {'executed_source_sha256': base.sha(executed_policy),
            'current_source_sha256': base.sha(trial.__file__),
            'correction': 'Tax, diluted shares and one-off expense evidence are eligible financial premise context. '
                'The originally rejected zero-growth Meta response was replayed without changing any model output.',
            'additional_provider_requests': 0, 'original_execution_report_unchanged': True},
        'parent_ledgers_and_source_files_unchanged': True,
        'acquisition_calls': 0, 'paid_search_calls': 0, 'old_budget_resets': 0,
        'probabilities_modified_during_audit': False, 'calibration_validated': False,
        'forecast_accuracy_not_evaluated': True, 'submitted': False, 'production_modified': False,
        'comparison_limits': ['Four development questions, not an unseen evaluation cohort.',
            'Independent Mercury raw coverage is identical; templates, instructions and diagnostic head count changed.',
            'Prior source-review work is reused. Cost reduction is conditional, not a new-question cold-start estimate.',
            'Open-tail quantiles retain boundary inequalities, never invented point estimates.'],
        'release_verification': verify_release(), 'execution_report_sha256': base.sha(root / 'report.json')}
    if ledger_hashes(root) != frozen:
        raise ValueError('Original execution archive changed during offline audit')
    save(output, result)
    print(__import__('json').dumps({k: v for k, v in result.items() if k not in
        ('questions', 'release_verification', 'post_generation_policy_audit')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    audit(args.root, args.output)
