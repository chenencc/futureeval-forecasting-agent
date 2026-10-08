"""Quote-bound assistant review of the initial three saved financial packages."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib

from ForecastAgent.competition.queue import save, load
from ForecastAgent.market_pulse.audit import run as audit


def run(root):
    root = Path(root)
    report = audit(root)
    expected = {'46190', '46197', '46191'}
    if {q['id'] for q in report['questions']} != expected:
        raise ValueError('Review is restricted to the original three-question pilot')
    reviewed = []
    for question in report['questions']:
        ident = question['id']
        rows = []
        for page in question['pages']:
            url = page['url']
            preview = page['preview']
            role, usable, reason = 'unreviewed', False, 'Source requires separate review.'
            if 'errors.edgesuite.net' in preview and 'Reference #' in preview:
                role, reason = 'cdn_error_shell', 'A CDN error reference is not issuer financial evidence.'
            elif 'calendar/earnings?symbol=AAPL' in url:
                role, reason = 'calendar_filter_not_honored', 'Saved body shows the October 8 daily calendar for other companies; the URL filter is not proof of issuer scope.'
            elif '/investor-relations/default.aspx' in url:
                role, reason = 'navigation_only', 'The saved body is a menu, not financial data or a report.'
            elif 'tsla-20251002-gen.pdf' in url and 'October 2, 2025' in preview:
                role, usable, reason = 'historical_operating_release', True, 'Official 2025 operating background; not the target 2026 GAAP EPS earnings release.'
            elif 'alphaquery.com/stock/TSLA/earnings-history' in url and 'before non-recurring' in preview:
                role, usable, reason = 'secondary_eps_history_and_estimate', True, 'Dated EPS history/estimate; the vendor definition excludes non-recurring items and cannot silently substitute for GAAP diluted EPS.'
            elif url == 'https://ir.tesla.com/press' and 'October 2, 2026' in preview:
                role, usable, reason = 'current_operating_drivers_and_detail_leads', True, 'Current production/delivery context and observed release links; full prior-quarter earnings tables and guidance still missing.'
            elif '/2026/01/apple-reports-first-quarter-results' in url and 'first quarter ended December 27, 2025' in preview:
                role, usable, reason = 'official_prior_fiscal_quarter', True, 'FY2026 Q1 history; not FY2026 Q4 revenue or its estimate.'
            elif '/320193/000032019326000020/aapl-20260627.htm' in url and 'June' in preview and '2026' in preview:
                role, usable, reason = 'official_latest_prior_quarter_10q', True, 'Apple FY2026 Q3 statement provides financial background; quarterly and nine-month columns and units must remain separate.'
            rows.append({'url': url, 'source_sha256': page['source_sha256'],
                         'review_role': role, 'usable_financial_or_operating_background': usable,
                         'reason': reason, 'frozen_excerpt': preview})
        relevant = sum(r['usable_financial_or_operating_background'] for r in rows)
        reviewed.append({'id': ident, 'title': question['title'],
                         'pipeline_status': question['state'],
                         'program_readable_pages': question['readable_pages'],
                         'reviewed_useful_background_pages': relevant,
                         'acquisition_readiness': 'insufficient' if relevant == 0 else 'partial',
                         'target_realized_value_expected_to_be_public_now': False,
                         'target_realized_value_is_not_required_for_prefinal_forecast': True,
                         'complete_current_financial_evidence_contract': False,
                         'missing_materials': ['Prior-quarter original financial tables',
                            'Current issuer guidance and exact target-period estimates',
                            'Verified basis/units and issuer-scoped detail-page coverage']
                             if ident != '46191' else ['Target-quarter dated revenue estimates',
                                'Current issuer revenue guidance', 'Quarter/unit normalization receipts'],
                         'resources': question['resources'], 'sources': rows})
    result = {'schema': 'market-pulse-three-pilot-reviewed-quality-v1',
              'reviewed_at_utc': datetime.now(timezone.utc).isoformat(),
              'review_method': 'Assistant reading of saved bodies; not a blind human review, truth certification, or forecast accuracy test.',
              'acquisition_version': 'v1.0.5-crawl4ai.1',
              'source_audit_sha256': hashlib.sha256((root / 'audit.json').read_bytes()).hexdigest(),
              'executed_runner_source_sha256': hashlib.sha256((root / 'executed-runner-source.py').read_bytes()).hexdigest(),
              'model_http_received': sum(a['status'] == 'received' for q in report['questions'] for a in q['provider_journals']),
              'reported_tokens': report['known_model_tokens'],
              'unknown_model_usage_attempts': report['unknown_model_usage_attempts'],
              'tavily_basic_attempts': report['logical_tavily_basic'], 'exa_logical_attempts': report['logical_exa'],
              'basic_extract_batches': sum((q.get('resources') or {}).get('extract_batches', 0) for q in report['questions']),
              'budget_checks': {'tavily_max_three_per_task': report['tavily_limit_respected'],
                               'exa_max_one_per_task': report['exa_limit_respected'],
                               'saved_task_budgets_reset': False},
              'analysis_run': False, 'submitted': False, 'questions': reviewed,
              'system_findings': [
                {'priority': 'P0', 'code': 'group_source_scope_leak',
                 'finding': 'Shared group criteria linked multiple issuers; every supplement spent attempts on AAPL calendar and AMD earnings, including Tesla tasks.',
                 'recommended_change': 'Retain full immutable rules but separate target issuer leads from other group member leads.'},
                {'priority': 'P0', 'code': 'future_release_is_not_current_research_target',
                 'finding': 'Critical plans/searches concentrate on the future target earnings release; current prior-quarter statements, operating releases, guidance and dated estimates need independent acquisition targets.',
                 'recommended_change': 'Separate future resolution artifact, currently published predictors, and explicitly not-yet-published material.'},
                {'priority': 'P0', 'code': 'reparse_quality_regression',
                 'finding': 'Saved CDN error references are promoted from rejected/thin bodies to usable_text during supplement reparse; financial navigation also inflates readable counts.',
                 'recommended_change': 'Preserve blocking diagnostics for identical content versions and expose semantic source roles separately from text length.'},
                {'priority': 'P1', 'code': 'missing_issuer_report_cache',
                 'finding': 'Tesla tasks have no shared useful finance package; their common capture hash is the unrelated calendar.',
                 'recommended_change': 'Share immutable, issuer/period-bound report captures across EPS and revenue without duplicating budget claims.'},
                {'priority': 'runner_fixed', 'code': 'local_transport_import_order',
                 'finding': 'Initial runner installed provider transport after release aliases were imported; transport receipts cover Extract, while model/Search source journals are preserved.',
                 'recommended_change': 'Install transport before importing release modules. Next runner implements this; offline alias regression test passes. No extra provider calls were spent validating the fix.'}]}
    manifest = load(root / 'manifest.json')
    if result['executed_runner_source_sha256'] != manifest['runner_sha256']:
        raise ValueError('Archived executed runner differs from the frozen manifest')
    save(root / 'quality-review.json', result)
    return result


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    result = run(args.root)
    print(json.dumps({'questions': [{k: q[k] for k in ('id', 'program_readable_pages',
                     'reviewed_useful_background_pages', 'acquisition_readiness')} for q in result['questions']],
                     'reported_tokens': result['reported_tokens'], 'submitted': False}, indent=2))
