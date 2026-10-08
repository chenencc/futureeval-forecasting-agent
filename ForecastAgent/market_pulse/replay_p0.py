"""Paired offline replay of the archived financial pilot, without provider calls."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import socket
from unittest.mock import patch

from ForecastAgent.competition.queue import load, save
from ForecastAgent.market_pulse.collection import acquisition_policy, overlay, prepare, policy_hashes
from ForecastAgent.market_pulse.financial import issuer_profile, page_scope


def file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def pages_report(bundle, profile):
    return [{'url': url, 'chars': len(page.get('content', '')),
             'content_sha256': hashlib.sha256(page.get('content', '').encode()).hexdigest(),
             'raw_sha256': page.get('sha256'), 'usable_text': page.get('body_diagnostics', {}).get('usable_text'),
             'body_state': page.get('body_diagnostics', {}).get('state'),
             'issuer_scope': page_scope(url, page, profile)}
            for url, page in bundle.get('pages', {}).items()]


def run(root, output):
    from ForecastAgent.releases.v1_0_5 import verify_release
    from ForecastAgent.runtime import source_frontier, gap_repair
    verify_release()
    output = output.resolve(); root = root.resolve()
    if output.is_relative_to(root) or output.exists():
        raise ValueError('Use a new output directory outside the immutable pilot')
    baseline_hashes = file_hashes(root)
    audit = load(root / 'quality-review.json')
    reviewed = {row['id']: row for row in audit['questions']}
    rows = []
    network_attempts = []
    def forbidden(*args, **kwargs):
        network_attempts.append('blocked')
        raise AssertionError('Network and model calls are forbidden during offline replay')
    with ExitStack() as guards:
        guards.enter_context(patch.object(socket.socket, 'connect', forbidden))
        guards.enter_context(patch.object(http.client.HTTPConnection, 'connect', forbidden))
        guards.enter_context(patch('ForecastAgent.providers.ultra.ask_ultra', forbidden))
        for ident in ('46190', '46197', '46191'):
            native = root / 'tasks' / ident / 'retrieval/release-1.0.5'
            raw = load(native / 'collection/bundle.json')
            baseline = load(native / 'package.json')
            profile = issuer_profile(raw['request'])
            before_frontier = source_frontier.unread_candidates(raw, limit=20)
            with acquisition_policy():
                after_frontier = source_frontier.unread_candidates(raw, limit=20)
                inventory = gap_repair.inventory(native / 'parent.zip')
                revised = overlay(raw, native / 'supplement', ident)
            # Original rules and budgets are bound separately from the new
            # policy field; no saved input or acquisition state is rewritten.
            prepared = prepare(raw['request'])
            for key in ('question', 'resolution_criteria', 'fine_print', 'background'):
                if prepared.get(key) != raw['request'].get(key):
                    raise AssertionError('Original question field changed')
            before = pages_report(baseline, profile)
            after = pages_report(revised, profile)
            for page in after:
                old = next((p for p in before if p['url'] == page['url']), None)
                if old is None or old['content_sha256'] != page['content_sha256']:
                    raise AssertionError('Offline replay changed an accepted original body')
            useful = {p['url'] for p in reviewed[ident]['sources']
                      if p['usable_financial_or_operating_background']}
            active = {p['url'] for p in after}
            false_rejections = sorted(useful - active)
            known_bad_accepted = sorted(active & {p['url'] for p in reviewed[ident]['sources']
                                        if not p['usable_financial_or_operating_background']})
            if false_rejections or known_bad_accepted:
                raise AssertionError('Reviewed positive or negative body regression failed')
            row = {'id': ident, 'issuer': profile['issuer_label'], 'metric': profile['metric'],
                   'before_pages': before, 'after_pages': after,
                   'before_reported_usable_pages': sum(p['usable_text'] is True for p in before),
                   'after_active_body_pages': len(after), 'original_question_fields_preserved': True,
                   'reviewed_useful_pages_retained': len(useful & active),
                   'reviewed_false_rejections': false_rejections,
                   'reviewed_bad_pages_still_active': known_bad_accepted,
                   'remaining_known_material_gaps': reviewed[ident]['missing_materials'],
                   'accepted_body_text_preserved': True,
                   'before_unread_frontier': before_frontier, 'after_unread_frontier': after_frontier,
                   'excluded_supplement_routes': inventory['financial_excluded_routes'],
                   'excluded_pages': pages_report({'pages': revised['financial_audit_pages']}, profile),
                   'capture_gaps': revised['capture_gaps'],
                   'research_contract': prepared['financial_acquisition_policy']['research'],
                   'semantic_complete': False, 'fresh_recall_improvement_measured': False}
            save(output / 'tasks' / ident / 'overlay.json', revised)
            rows.append(row)
    unchanged = baseline_hashes == file_hashes(root)
    if not unchanged or network_attempts:
        raise AssertionError('Preservation or offline boundary failed')
    report = {'schema': 'financial-p0-paired-offline-replay-v1',
              'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'source_pilot': str(root), 'source_files_sha256': baseline_hashes,
              'replay_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'policy_source_sha256': policy_hashes(), 'rows': rows,
              'before_reported_usable_pages': sum(r['before_reported_usable_pages'] for r in rows),
              'after_active_body_pages': sum(r['after_active_body_pages'] for r in rows),
              'source_files_unchanged': unchanged, 'baseline_review_useful_background_pages':
                  sum(q['reviewed_useful_background_pages'] for q in audit['questions']),
              'new_provider_calls': {'models': 0, 'tavily': 0, 'exa': 0, 'free_http': 0},
              'new_tokens': 0, 'budget_reset': False, 'analysis_run': False, 'submitted': False,
              'limits_unchanged': {'tavily_basic_lifetime_max': 3, 'exa_lifetime_max': 1},
              'limitations': ['Offline routing and body acceptance regression only; no new evidence was fetched.',
                  'Active body pages are useful research context, not verified target-quarter metrics.',
                  'New planning guidance is delivered to the model, but improved agent search behavior still requires a bounded live pilot.',
                  'Historical enforcement, metric definitions, units, timing, and forecast scoring were not relaxed.']}
    save(output / 'report.json', report)
    return {key: report[key] for key in ('before_reported_usable_pages', 'after_active_body_pages',
            'source_files_unchanged', 'new_provider_calls')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.output), indent=2))
