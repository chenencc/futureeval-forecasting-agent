"""Offline audit/recovery of additive saved-source analysis; no network calls."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from ForecastAgent.market_pulse import saved_source_trial as trial, compact_trial as compact
from ForecastAgent.market_pulse import source_coverage as coverage, analysis as base, derivation
from ForecastAgent.market_pulse.financial_recovery import ledger_hashes
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.competition.mercury import distribution_spec
from ForecastAgent.analysis import mercury_nonbinary_trial as typed
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.releases.v1_0_5 import verify_release, validate_payload


def audit(root, destination):
    manifest = load(root / 'manifest.json'); compact.prior.source_files_unchanged(manifest['inputs'])
    for parent, expected in manifest['parent_hashes'].items():
        if ledger_hashes(Path(parent)) != expected:
            raise ValueError('Original parent records changed')
    if base.sha(manifest['comparison_path']) != manifest['comparison_sha256']:
        raise ValueError('Frozen comparison changed')
    if base.sha(manifest['baseline_path']) != manifest['baseline_sha256']:
        raise ValueError('Frozen source-review baseline changed')
    baseline = load(manifest['baseline_path']); comparison = load(manifest['comparison_path'])
    execution = load(root / 'report.json'); questions = []
    for item in manifest['inputs']:
        if item['id'] not in manifest['selected']:
            # Other cases keep exactly the existing template, variables, raw
            # exposure and final probabilities. No live inference is replayed.
            b, vs, t, s, _ = compact.prepare(item, baseline)
            old_state = load(root / 'manifest.json')['parent_hashes']
            previous_root = next(Path(p) for p in old_state
                if (Path(p) / 'tasks' / item['id'] / 'source-state.json').exists())
            actual = load(previous_root / 'tasks' / item['id'] / 'source-state.json')
            for field in ('original_question', 'variables', 'original_evidence_library'):
                if digest(actual[field]) != digest(s[field]):
                    raise ValueError('Other-case frozen exposure changed')
            continue
        folder = root / 'tasks' / item['id']; bundle = load(item['package']); originals = load(item['variables'])
        supplement = load(folder / 'supplement-package.json'); state = load(folder / 'source-state.json')
        packet = load(folder / 'extraction-packet.json'); template = load(folder / 'template.json')
        vs = supplement['variables']; new_ids = supplement['added_variable_ids']
        table = load(folder / 'candidate-table.json'); selected = load(folder / 'extraction/raw.json')['facts']
        rebuilt, ids, _ = coverage.append_bound(bundle, originals, table, selected, packet)
        if rebuilt != vs or new_ids != ids or vs[:len(originals)] != originals:
            raise ValueError('Additive original token bindings differ')
        _, _, _, old_state, _ = compact.prepare(item, baseline)
        rebuilt_state = trial.augmented_state(bundle, vs, old_state, packet, ids)
        if rebuilt_state != state:
            raise ValueError('Original source exposure reconstruction differs')
        request = load(folder / 'mercury/request.json')
        if request['state'] != state or request['questions'] != compact.outcome_questions(distribution_spec(bundle['request'])):
            raise ValueError('Independent decision request differs')
        raw = load(folder / 'mercury/response.json')
        forecast = typed.forecast(raw, distribution_spec(bundle['request']))
        candidate = payload(bundle['request'], {'continuous_cdf': forecast['raw_cdf']})
        validate_payload(bundle['request'], candidate)
        uncertainty = compact.uncertainty_audit(bundle['request'], candidate, template)
        old = next(q for q in comparison['questions'] if q['id'] == item['id'])
        projection = compact.evaluate_assumptions(load(folder / 'super/raw.json'), template,
            vs, state['allowed_variable_ids']) if (folder / 'super/raw.json').exists() else None
        # An observed component is retained; future removal/recurrence remains
        # unknown. Flag limited rationale coverage without altering outputs.
        disclosures = [v['fact_id'] for v in vs if v['fact_id'] in ids and v['metric'] == 'one_off']
        reason = projection['assumptions']['growth_reason'] if projection else ''
        acknowledged = bool(re.search(r'refund|one[- ]time|one[- ]off|non[- ]recurr|adjust', reason, re.I))
        flags = ['Super growth rationale does not discuss disclosed historical one-off components.'] if disclosures and projection and not acknowledged else []
        row = {'id': item['id'], 'issuer': base.contract(bundle['request'])['issuer'],
            'status': 'completed_with_review', 'added_facts': [v for v in vs if v['fact_id'] in ids],
            'original_fact_count': len(originals), 'final_fact_count': len(vs),
            'coverage_before': packet['coverage'], 'coverage_after': state['saved_source_coverage'],
            'model_projection': projection, 'analyst_rationale_flags': flags,
            'program_baseline': derivation.evaluate(compact.zero_choice(template), [template]),
            'independent_quantiles': uncertainty['quantiles'],
            'previous_quantiles': trial.comparison_quantiles(old), 'uncertainty': uncertainty,
            'payload': candidate, 'payload_sha256': digest(candidate), 'cdf_format_valid': True,
            'mercury_blind_to_analyst_output': True,
            'old_original_exposure_and_variables_preserved': True,
            'new_semantic_interpretations_verified': False, 'forecast_accuracy_not_evaluated': True,
            'original_execution_result': load(folder / 'result.json'),
            'offline_report_recovery_only': True, 'additional_provider_calls': 0, 'submitted': False}
        save(folder / 'audited-result.json', row); questions.append(row)
    journals = [(p, load(p)) for p in (root / 'tasks').glob('*/**/http/*.json')]
    transport = [load(p) for p in (root / 'provider-transport/openrouter/attempts').glob('*.json')]
    observed = []; models = {}
    for p, j in journals:
        response = j.get('response') or {}; model = response.get('model') or j.get('request', {}).get('model')
        models[model] = models.get(model, 0) + 1
        observed.append({'path': str(p.relative_to(root)), 'model': model,
            'status': j.get('status'), 'http_status': j.get('http_status'),
            'requested_model': j.get('request', {}).get('model'),
            'response_id': response.get('id'), 'usage': response.get('usage')})
    if len(journals) != execution['logical_requests'] or len(transport) != execution['actual_physical_requests']:
        raise ValueError('Actual request counts differ from execution archive')
    result = {'protocol': 'financial-additive-saved-source-audit-v1',
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'questions': questions,
        'models': models, 'actual_http_attempts': observed, 'actual_physical_requests': len(transport),
        'usage': base.usage_audit([j for _, j in journals]),
        'offline_other_case_checks': load(root / 'preflight.json'),
        'other_cases_original_source_exposure_templates_and_predictions_unchanged': True,
        'other_cases_provider_calls': 0, 'original_sources_facts_provider_records_unchanged': True,
        'old_budget_resets': 0, 'additional_recovery_provider_calls': 0,
        'search_fetch_calls': 0, 'release_verification': verify_release(),
        'calibration_validated': False, 'forecast_accuracy_not_evaluated': True,
        'comparison_limit': 'Expanded saved-source exposure and new stochastic draws. Not a same-exposure controlled model comparison or proof of accuracy improvement.',
        'remaining_scope_limits': ['Unknown fiscal periods and table-column relationships remain explicit gaps.',
            'New extracted metric, basis and period interpretations are provisional, not independent semantic verification.',
            'Historical one-off disclosures do not prove future recurrence or removal.'],
        'production_modified': False, 'submitted': False, 'execution_report_sha256': base.sha(root / 'report.json')}
    if destination.exists():
        raise ValueError('Final independent audit already exists; do not replace it')
    save(destination, result)
    print(json.dumps({'questions': len(questions), 'actual_requests': len(transport),
        'usage': result['usage'], 'quantiles': [q['independent_quantiles'] for q in questions],
        'saved': str(destination)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    audit(args.root, args.destination)
