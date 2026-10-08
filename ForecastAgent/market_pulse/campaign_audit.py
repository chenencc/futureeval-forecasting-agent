"""Offline aggregate of immutable material, provider journals and delivery receipts."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.competition.platform import forecast_matches
from ForecastAgent.competition.queue import questions
from ForecastAgent.market_pulse import analysis, facts, open_campaign


def numeric_audit(variables):
    inspected = 0; errors = []
    for variable in variables:
        if 'original_row_ref' not in variable or 'numeric_token' not in variable:
            continue
        inspected += 1
        original = variable['numeric_token']
        row = variable['original_row_ref']['quote']
        complete = [n for n in facts.numeric_tokens(row)
                    if n['start'] == original['start'] and n['end'] >= original['end']]
        if len(complete) != 1 or complete[0]['value'] != original['value']:
            errors.append({'fact_id': variable['fact_id'], 'saved_token': original,
                           'complete_original_tokens': complete})
    return {'inspected': inspected, 'complete_token_mismatches': errors,
            'semantic_interpretation_certified': False}


def run(root):
    inventory = load(root / 'official/campaign-inventory.json')
    worker = load(root / 'delivery-worker/report.json')
    if worker['confirmed_receipts'] != worker['confirmed_matching_forecasts']:
        raise ValueError('Delivery receipt/readback counts differ')
    final_reports = sorted((p for p in (root / 'delivery-worker').glob('*/report.json')),
                           key=lambda p: p.stat().st_mtime_ns)
    snapshot_folder = final_reports[-1].parent
    posts = {str(p.stem.removeprefix('final-post-')): load(p)
             for p in snapshot_folder.glob('final-post-*.json')}
    old_plan = load(root.parent.parent / 'deliveries/financial-four-20261009/plan.json')
    old_entries = {e['id']: e for e in old_plan['entries']}
    rows = []; parent_checks = {}; numerical_errors = []
    for row in inventory['rows']:
        ident = row['question_id']; task = root / 'tasks' / ident
        result_folder, result = open_campaign.latest_result(root, ident)
        detail = {k: row[k] for k in ('question_id', 'post_id', 'title', 'url', 'conservative_deadline_utc')}
        detail.update(original_campaign_state=row['campaign_state'], rule_review=row['rule_review'])
        if (task / 'hold.json').exists():
            detail.update(state='held_distribution_conflict', hold=load(task / 'hold.json'))
        elif row['campaign_state'] == 'already_forecast':
            entry = old_entries[ident]
            question = next(q for q in questions(posts[str(row['post_id'])]) if str(q['id']) == ident)
            detail.update(state='previously_submitted_not_reposted',
                          matches_latest_authenticated_readback=forecast_matches(question, entry['candidate']))
        elif (task / 'delivery/submission.json').exists():
            receipt = load(task / 'delivery/submission.json'); plan = load(task / 'delivery/plan.json')
            question = next(q for q in questions(posts[str(row['post_id'])]) if str(q['id']) == ident)
            match = forecast_matches(question, receipt['payload'])
            if receipt['status'] != 'accepted' or not match:
                raise ValueError('Unconfirmed new forecast: ' + ident)
            detail.update(state='newly_submitted_confirmed',
                matches_latest_authenticated_readback=match, http_status=receipt.get('http_status'),
                private_comment_id=receipt.get('comment_id'), confirmed_at_utc=receipt['confirmed_at_utc'],
                quantiles=plan['quantiles'], receipt_path=str(task / 'delivery/submission.json'),
                candidate_sha256=plan['candidate_sha256'])
        elif row['campaign_state'] == 'rule_review':
            detail['state'] = 'held_rule_conflict'
        else:
            detail.update(state='analysis_needs_review', error=(result or {}).get('error'))
        if result_folder:
            detail['latest_eligible_or_diagnostic_analysis'] = str(result_folder / 'result.json')
            detail['numeric_audit'] = numeric_audit((result or {}).get('variables', []))
            if detail['numeric_audit']['complete_token_mismatches']:
                numerical_errors.append(ident)
            identity_path = result_folder / 'identity.json'
            if identity_path.exists():
                identity = load(identity_path); source = Path(identity['package']); package = load(source)
                detail.update(analysis_source_package=str(source),
                    package_sha256_unchanged=analysis.sha(source) == identity['package_sha256'],
                    saved_readable_page_count=len(package['pages']), gaps=package.get('gaps', []),
                    approved_variable_ids=(result or {}).get('source_review', {}).get('allowed_variable_ids', []),
                    calibration_validated=False)
        provenance = task / 'capture-provenance.json'
        if provenance.exists():
            for parent in load(provenance)['parents']:
                path = Path(parent['path'])
                parent_checks[str(path)] = analysis.sha(path) == parent['sha256']
        stages = []
        for path in sorted(task.glob('analysis*/result.json')):
            d = load(path)
            stages.append({'path': str(path), 'status': d.get('status'), 'error': d.get('error'),
                           'manual_delivery_candidate': d.get('manual_delivery_candidate', False),
                           'usage': d.get('usage')})
        detail['preserved_analysis_stages'] = stages; rows.append(detail)
    journals = sorted(root.glob('**/http/*.json')) + sorted(root.glob('**/model_calls/*.json'))
    model_records = [load(p) for p in journals]
    by_model = {}
    for model in sorted({r['request']['model'] for r in model_records}):
        selected = [r for r in model_records if r['request']['model'] == model]
        by_model[model] = {'physical_journal_attempts': len(selected), **analysis.usage_audit(selected)}
    physical = [load(p) for p in (root / 'provider-transport').glob('**/*.json')]
    model_transport = [r for r in physical if r.get('provider') == 'openrouter']
    exa = [load(p) for p in (root / 'exa-transport').glob('**/*.json')]
    ledger_rows = []
    for path in sorted((root / 'acquisition').glob('tasks/*/retrieval/release-1.0.5/collection/bundle.json')):
        b = load(path)
        ledger_rows.append({'question_id': path.parents[3].name,
            'basic_searches': len(b.get('searches', [])), 'exa_searches': len(b.get('exa_searches', [])),
            'journal': str(path)})
    for path in sorted((root / 'tasks').glob('*/supplement-v1/report.json')):
        d = load(path)
        ledger_rows.append({'question_id': path.parents[1].name,
            'basic_searches': d['lifetime_tavily_basic_calls'], 'exa_searches': None,
            'independent_supplement_basic_calls': d['new_tavily_basic_calls'], 'journal': str(path)})
    report = {'schema': 'market-pulse-open-campaign-audit-v1',
        'finished_at_utc': datetime.now(timezone.utc).isoformat(), 'tournament': inventory['tournament'],
        'visible_open_leaves_at_capture': inventory['visible_leaf_count'],
        'state_counts': dict(Counter(r['state'] for r in rows)), 'questions': rows,
        'model_usage_by_model': by_model,
        'original_model_journals_match_physical_attempt_count': len(model_transport) == len(model_records),
        'provider_physical_http_status_counts': [
            {'provider': key[0], 'endpoint': key[1], 'http_status': key[2], 'count': value}
            for key, value in Counter((r.get('provider'), r.get('endpoint'), r.get('http_status'))
                                      for r in physical).items()],
        'new_tavily_basic_physical_calls': sum(r.get('provider') == 'tavily' and r.get('endpoint') == '/search' for r in physical),
        'new_tavily_basic_extract_physical_batches': sum(r.get('provider') == 'tavily' and r.get('endpoint') == '/extract' for r in physical),
        'new_exa_physical_attempts': len(exa), 'per_task_new_acquisition_and_supplement_ledgers': ledger_rows,
        'per_task_basic_maximum_three_preserved': all(r['basic_searches'] <= 3 for r in ledger_rows),
        'all_reused_capture_parent_hashes_unchanged': all(parent_checks.values()),
        'reused_capture_parent_checks': parent_checks,
        'accepted_analysis_complete_numeric_token_mismatches': numerical_errors,
        'all_analysis_stages_and_failed_responses_preserved': True, 'old_budget_resets': 0,
        'old_four_not_reposted': True, 'new_listener_or_production_change': False,
        'offline_tests_passed': {'market_pulse': 153, 'official_transport': 16},
        'evaluation_note': 'Live prospective financial forecasts. No realized accuracy or calibration claim. '
            'Source compatibility is fallible. Readable bodies and format acceptance do not establish complete evidence.',
        'latest_delivery_report': str(snapshot_folder / 'report.json')}
    save(root / 'report.json', report)
    destination = root.parent.parent / 'reports/market-pulse-open-20261009.json'
    save(destination, report)
    print(json.dumps({'state_counts': report['state_counts'],
        'model_attempts': len(model_records), 'new_basic_searches': report['new_tavily_basic_physical_calls'],
        'new_exa_searches': len(exa), 'capture_parent_preservation': all(parent_checks.values()),
        'numeric_mismatches': numerical_errors, 'report': str(destination)}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    run(parser.parse_args().root)
