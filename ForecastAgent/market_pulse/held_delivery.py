"""One-off, explicitly authorized delivery of three reviewed guidance CDFs.

The preview remains immutable and held for automatic delivery. This separate
manual operation binds the user's approval to exact rules and candidates,
preserves disclosed diagnostics and uses the existing receipt-safe transport.
There are no model, search, scheduling or production changes.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.competition.platform import deliver, forecast_matches
from ForecastAgent.competition.queue import questions, utc
from ForecastAgent.market_pulse import analysis, guidance, inventory
from ForecastAgent.market_pulse.held_preview import protected
from ForecastAgent.market_pulse.manual_delivery import PacedClient, compare, metadata
from ForecastAgent.releases.v1_0_5 import validate_payload, verify_release
from ForecastAgent.runtime.task_lock import task_lock

IDS = ('46248', '46249', '46250')
VERSION = 'market-pulse-reviewed-guidance-manual-delivery-v1'
REVIEW_CODES = ('guidance_quarter_conflict', 'guidance_release_date_requires_review')


def quantile_text(q, unit):
    if q['value'] is not None:
        return f"{q['value']:g} {unit}"
    side = 'above' if q['state'] == 'above_platform_range' else 'below'
    return f"{side} {q['boundary']:g} {unit}; exact open-tail quantile unavailable"


def prepare(root, preview, campaign, commit):
    report_path = preview / 'report.json'
    report = load(report_path)
    if protected(campaign) != load(preview / 'protected-originals.json'):
        raise ValueError('Protected original campaign changed')
    if not report['originals_unchanged'] or report['new_submission_calls'] != 0:
        raise ValueError('An unchanged, unsubmitted preview is required')
    entries = []
    for ident in IDS:
        row = next(r for r in report['rows'] if r['id'] == ident)
        request_path = preview / 'official/inputs' / f'{ident}.json'
        request = load(request_path)['request']
        if row['state'] != 'preview_complete' or not row.get('mercury'):
            raise ValueError('Reviewed Mercury candidate missing: ' + ident)
        candidate = row['mercury']['candidate']
        validate_payload(request, candidate)
        target = guidance.contract(request)
        state_path = preview / 'tasks' / ident / 'state.json'
        state = load(state_path)
        sources = sorted({r['url'] for r in state['original_evidence_library'] if r.get('url')})
        disagreement = row['final_mercury_region_audit']['maximum_absolute_probability_difference']
        qs = row['display_quantiles']
        comment = (
            '# ForecastAgent — Market Pulse 26Q4\n\n'
            f'## Target\n{request["question"]}\n\n'
            f'Interpret the numeric target as {target["target_guidance_period"]} guidance '
            f'in the {target["publishing_release_period"]} release. Apply the original '
            'primary midpoint/single-figure and rounding rules, conditional on valid numeric resolution. '
            'The Q3-in-Q2 no-guidance clause is treated as an administrative annulment prerequisite. '
            'The original expected August 26 publication date remains unresolved; this interpretation '
            'is not official staff clarification. Previously published Q3 guidance is a predictor, '
            'not the future Q4 resolving figure.\n\n'
            '## Predictive distribution\n' + '\n'.join(
                '- ' + name + ': ' + quantile_text(qs[p], request['unit'])
                for name, p in [('10th percentile', '0.1'), ('Median', '0.5'), ('90th percentile', '0.9')])
            + '\n\n## Method and limitations\n'
            'Submit the exact previously displayed independent Mercury CDF from saved original '
            'issuer evidence. No Super forecast, program midpoint or other head probabilities are '
            'averaged into it. Keep GAAP basis, fiscal periods and financial units explicit. '
            'Retain both open tails; CDF probabilities are bounded to [0.02, 0.98]. '
            'Platform cutpoints are not an assumed center or a cap on possible outcomes. '
            f'The coarse/fine region diagnostic differs by up to {100*disagreement:.1f} percentage points; '
            'its experimental threshold is not an official submission requirement or an accuracy score. '
            'No forecast-error history, probability calibration or prospective accuracy has been validated. '
            'Super repairs remain diagnostic and are not substituted into this distribution.\n\n'
            '## Original sources\n' + '\n'.join('- ' + u for u in sources)
            + f'\n\nDevelopment commit: {commit}. Explicit manual submission approved after the preview review; '
            'automatic holds and original records remain preserved.'
        )
        result_path = preview / 'tasks' / ident / 'result.json'
        entries.append({'id': ident, 'post_id': 46016,
            'package': str(request_path), 'package_sha256': analysis.sha(request_path),
            'audit_path': str(report_path), 'audit_sha256': analysis.sha(report_path),
            'payload_path': str(result_path), 'payload_file_sha256': analysis.sha(result_path),
            'state_path': str(state_path), 'state_sha256': analysis.sha(state_path),
            'candidate': candidate, 'candidate_sha256': digest(candidate), 'comment': comment,
            'issuer': 'NVIDIA', 'metric': target['metric'],
            'target_period': target['target_guidance_period'], 'quantiles': qs,
            'manual_review_authorized': True, 'reviewed_codes': list(REVIEW_CODES),
            'known_region_disagreement': disagreement,
            'unresolved_date_disclosed': True, 'does_not_certify_accuracy': True})
    plan = {'schema': VERSION, 'tournament': inventory.SLUG, 'project_id': inventory.PROJECT_ID,
        'source_commit': commit, 'preview': str(preview), 'campaign': str(campaign),
        'entries': entries, 'excluded_ids': ['46198'],
        'authorization': 'User explicitly requested submission of the displayed previews and then continuation on 2026-10-09.',
        'automatic_holds_not_removed': True, 'new_model_calls': 0, 'new_search_calls': 0,
        'production_changes': False}
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'plan.json').exists() and load(root / 'plan.json') != plan:
        raise ValueError('Frozen authorized plan changed')
    save(root / 'plan.json', plan)
    print({'prepared': list(IDS), 'excluded': ['46198'], 'submitted': False}, flush=True)
    return plan


def authorized_check(entry, meta, post):
    if entry['id'] not in IDS or entry['post_id'] != 46016 or not entry['manual_review_authorized']:
        raise ValueError('Outside the explicitly authorized guidance scope')
    if analysis.sha(entry['state_path']) != entry['state_sha256']:
        raise ValueError('Original evidence exposure changed')
    inspected, inputs = inventory.inspect(meta, [post], utc().isoformat())
    row = next(r for r in inspected['rows'] if r['question_id'] == entry['id'])
    codes = sorted({v['code'] for v in row['rule_review']})
    if codes != sorted(entry['reviewed_codes']) or codes != sorted(REVIEW_CODES):
        raise ValueError('Fresh review conditions differ from the authorized preview')
    if not row['platform_open_with_permission'] or not row['input_valid']:
        raise ValueError('Platform status, permission or input shape does not permit delivery')
    if not row['conservative_deadline_utc'] or inventory.instant(row['conservative_deadline_utc']) <= utc():
        raise ValueError('Official conservative deadline has passed')
    live = inputs[entry['id']]['request']
    target = guidance.contract(live)
    if target['target_guidance_period'] != entry['target_period'] or target['metric'] != entry['metric']:
        raise ValueError('Official numeric guidance target changed')
    # This is a scoped user-reviewed operation, not automatic certification.
    # Compare still requires exact frozen source/rule/payload hashes.
    manually_approved_row = {**row, 'rule_review': [], 'automatic_candidate': True}
    q = next(q for q in questions(post) if str(q['id']) == entry['id'])
    check = compare(entry, live, manually_approved_row, q)
    check.update(manual_review_authorized=True, reviewed_codes=codes,
        original_rule_review_preserved=row['rule_review'], automatic_campaign_eligibility_not_changed=True,
        unresolved_date_disclosed=True, known_region_disagreement=entry['known_region_disagreement'])
    return check


def run(root, execute=False):
    plan = load(root / 'plan.json')
    if (plan['schema'] != VERSION or plan['tournament'] != inventory.SLUG
            or plan['project_id'] != inventory.PROJECT_ID
            or tuple(e['id'] for e in plan['entries']) != IDS):
        raise ValueError('Unexpected authorized plan identity')
    verify_release()
    with task_lock(root):
        if protected(Path(plan['campaign'])) != load(Path(plan['preview']) / 'protected-originals.json'):
            raise ValueError('Protected original campaign changed')
        client = PacedClient(os.environ['METACULUS_TOKEN'], root / 'read-backoff')
        stage = root / 'preflight' / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%fZ')
        account = client.account(); meta = metadata(client)
        save(stage / 'account.json', account); save(stage / 'metadata.json', meta)
        if meta.get('bot_leaderboard_status') != 'include' or 'Bot Participation Rules' not in meta.get('description', ''):
            raise ValueError('Official bot participation rules changed')
        post = client.post(46016); save(stage / 'post-46016.json', post)
        checked = [authorized_check(e, meta, post) for e in plan['entries']]
        save(stage / 'report.json', {'checked_at_utc': utc().isoformat(), 'rows': checked,
            'execute': execute, 'no_model_calls': True, 'all_exact_candidates_format_valid': True})
        print({'preflight_valid': True, 'ids': list(IDS), 'execute': execute}, flush=True)
        if not execute:
            return
        results = []
        for entry in plan['entries']:
            try:
                fresh = client.post(entry['post_id'])
                check = authorized_check(entry, meta, fresh)
                folder = root / 'tasks' / entry['id']
                if check['existing_forecast'] and not (folder / 'submission.json').exists():
                    receipt = {'status': 'existing_forecast_not_overwritten',
                        'matches_candidate': check['existing_forecast_matches']}
                else:
                    receipt = deliver(client, {'id': entry['id'], 'post_id': entry['post_id'],
                        'analysis_commit': plan['source_commit'], 'analysis_release_version': VERSION},
                        entry['candidate'], entry['comment'], folder, enabled=True)
                result = {'id': entry['id'], 'receipt': receipt, 'quantiles': entry['quantiles']}
            except Exception as exc:
                result = {'id': entry['id'], 'status': 'needs_review', 'error_type': type(exc).__name__,
                    'error': str(exc), 'state_preserved': True, 'no_blind_duplicate_post': True}
            results.append(result); save(root / 'progress.json', {'results': results})
            print({'id': entry['id'], 'status': result.get('receipt', {}).get('status', result.get('status'))}, flush=True)
        final = client.post(46016); save(stage / 'final-post-46016.json', final)
        comments = client.comments(46016); save(stage / 'final-private-comments.json', comments)
        verified = []
        for entry in plan['entries']:
            q = next(q for q in questions(final) if str(q['id']) == entry['id'])
            record_path = root / 'tasks' / entry['id'] / 'submission.json'
            receipt = load(record_path) if record_path.exists() else {}
            matching = [c for c in comments if receipt.get('marker') and receipt['marker'] in (c.get('text') or '')
                and c.get('is_private') is True and (c.get('author') or {}).get('id') == account['id']]
            verified.append({'id': entry['id'], 'matches_authenticated_platform': forecast_matches(q, entry['candidate']),
                'private_comment_matches': bool(matching), 'comment_ids': [c['id'] for c in matching],
                'forecast_start_time': ((q.get('my_forecasts') or {}).get('latest') or {}).get('start_time')})
        report = {'schema': VERSION, 'finished_at_utc': utc().isoformat(), 'account': account,
            'results': results, 'readback': verified,
            'confirmed_matching_forecasts': sum(v['matches_authenticated_platform'] for v in verified),
            'confirmed_matching_private_comments': sum(v['private_comment_matches'] for v in verified),
            'accepted_receipts': sum(r.get('receipt', {}).get('status') == 'accepted' for r in results),
            'new_model_calls': 0, 'new_search_calls': 0, 'excluded_annulled_question': '46198',
            'automatic_holds_preserved': True, 'production_changed': False}
        report['original_campaign_unchanged'] = protected(Path(plan['campaign'])) == load(Path(plan['preview']) / 'protected-originals.json')
        save(root / 'report.json', report)
        save(root.parent.parent / 'reports/market-pulse-guidance-delivery-20261009.json', report)
        print({'confirmed_forecasts': report['confirmed_matching_forecasts'],
            'confirmed_private_comments': report['confirmed_matching_private_comments']}, flush=True)
        return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'preflight', 'submit'])
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--preview', type=Path)
    p.add_argument('--campaign', type=Path)
    p.add_argument('--commit')
    args = p.parse_args()
    if args.command == 'prepare':
        if not args.preview or not args.campaign or not args.commit:
            p.error('prepare requires preview, campaign and commit')
        prepare(args.root, args.preview, args.campaign, args.commit)
    else:
        run(args.root, execute=args.command == 'submit')
