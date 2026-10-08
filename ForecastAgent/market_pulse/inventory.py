"""Read official rules and prepare blind acquisition inputs, without submission."""
import csv
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ForecastAgent.acquisition.pipeline import prepare
from ForecastAgent.analysis.distributions import payload, range_metadata
from ForecastAgent.competition.live import live_request
from ForecastAgent.competition.platform import Client
from ForecastAgent.competition.queue import digest, questions, save
from ForecastAgent.releases.surfaces import normalize

SLUG = 'market-pulse-26q4'
PROJECT_ID = 33131
BASELINE_TAG = 'v1.0.5-crawl4ai.1'
BASELINE_COMMIT = 'ba94cea523528b98b8fa67ebb0b9dd0ff3d626bc'
API = 'https://www.metaculus.com/api/'


def instant(value):
    """Require an explicit timezone in platform timestamps."""
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Platform timestamp has no timezone')
    return result.astimezone(timezone.utc)


def content_sources(post, original):
    group = post.get('group_of_questions') or {}
    return {name: ('question' if original.get(name) else 'group_of_questions'
                   if group.get(name) else 'post' if post.get(name) else 'missing')
            for name in ('description', 'resolution_criteria', 'fine_print')}


def review_issues(question):
    """Apply a disclosed, quote-bound manual audit; never rewrite official rules.

    These are observations from the initial tournament audit, not a general
    semantic rule checker. New or changed rules require a new manual audit.
    """
    issues = []
    title = question.get('title', '')
    criteria = question.get('resolution_criteria', '')
    fine = question.get('fine_print', '')
    if ('Q3 FY2027' in title and 'Q4 FY2027' in criteria
            and 'Q3 FY2027' in fine and 'Q2 FY2027' in fine):
        issues.append({'code': 'guidance_quarter_conflict',
                       'detail': 'Title/criteria target Q4 guidance in Q3 release; fine print refers to Q3 guidance in Q2 release.',
                       'fields': ['title', 'resolution_criteria', 'fine_print']})
    if 'Q3 FY2027' in title and re.search(r'August\s+26,?\s+2026', criteria):
        issues.append({'code': 'guidance_release_date_requires_review',
                       'detail': 'The stated expected August release date needs confirmation against the target fiscal release.',
                       'fields': ['title', 'resolution_criteria']})
    # The shared criteria contains issuer-specific expected dates. Do not
    # silently use a sibling EPS deadline to change a revenue deadline.
    if question.get('label', '').upper() == 'AMD' and 'revenues' in title.lower():
        if re.search(r'AMD[^\n]*November\s+3', criteria, flags=re.I):
            close = question.get('actual_close_time') or question.get('scheduled_close_time')
            if close and instant(close).date().isoformat() < '2026-11-03':
                issues.append({'code': 'issuer_release_after_close',
                               'detail': 'AMD expected release is November 3; the official revenue close is earlier.',
                               'fields': ['resolution_criteria', 'actual_close_time', 'scheduled_close_time']})
    return issues


def financial_profile(question):
    text = question['title'].lower()
    metric = ('gaap_diluted_eps' if 'earnings per share' in text else
              'reported_quarterly_revenue' if 'revenues' in text else
              'forward_guidance' if 'guidance' in text else 'unclassified')
    unit = question.get('unit')
    links = list(dict.fromkeys(re.findall(r'https?://[^\s<>"\)]+',
        question.get('resolution_criteria', '') + '\n' + question.get('description', ''))))
    return {'schema': 'market-pulse-financial-profile-v1', 'metric': metric,
            'issuer_label': question.get('label') if metric != 'forward_guidance' else 'NVIDIA',
            'target_unit': unit, 'fiscal_period': None, 'verified_ticker': None,
            'verified_cik': None, 'rule_links': links,
            'required_evidence': ['issuer_identity', 'fiscal_reporting_period',
                'original_earnings_release', 'metric_definition_and_units',
                'first_publication_timestamp', 'prior_quarter_history',
                'prior_management_guidance', 'dated_consensus_if_available'],
            'unknowns_are_not_inferred': True,
            'search_budget': {'tavily_basic_lifetime_max': 3, 'exa_lifetime_max': 1},
            'source_roles': ['official_actual', 'management_guidance',
                             'analyst_estimate', 'independent_context']}


def inspect(metadata, posts, now):
    if metadata.get('slug') != SLUG or metadata.get('id') != PROJECT_ID:
        raise ValueError('Wrong tournament identity')
    now = instant(now)
    rows, inputs, seen = [], {}, set()
    for raw_post in posts:
        post = normalize(raw_post)
        originals = {str(q['id']): q for q in questions(raw_post)}
        for question in questions(post):
            ident = str(question['id'])
            if ident in seen:
                raise ValueError('Duplicate question identity across posts')
            seen.add(ident)
            times = {k: question.get(k) for k in
                     ('actual_close_time', 'scheduled_close_time', 'spot_scoring_time')}
            available = [instant(t) for t in times.values() if t]
            deadline = min(available).isoformat() if available else None
            permission = post.get('user_permission') or metadata.get('user_permission')
            open_now = (question.get('status') == 'open'
                        and (not question.get('open_time') or instant(question['open_time']) <= now))
            platform_open = open_now and permission in ('forecaster', 'curator', 'admin', 'creator')
            issues = review_issues(question)
            errors = []
            probe = None
            request = None
            try:
                request, _ = prepare(live_request(post, question))
                request['tournament'] = SLUG
                # Validate shape using a synthetic distribution, never a forecast.
                meta = range_metadata(request)
                count = meta['inbound_outcome_count']
                probe = payload(request, {'continuous_cdf': [i / count for i in range(count + 1)]})
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
            automatic = bool(platform_open and deadline and instant(deadline) > now
                             and not errors and not issues)
            row = {'question_id': ident, 'post_id': post['id'], 'title': question.get('title'),
                   'label': question.get('label'), 'type': question.get('type'),
                   'unit': question.get('unit'), 'status': question.get('status'),
                   'permission': permission, 'platform_open_with_permission': platform_open,
                   'conservative_deadline_utc': deadline, 'official_times': times,
                   'scoring_time_differs_from_close': bool(times['spot_scoring_time']
                       and times['spot_scoring_time'] != (times['actual_close_time'] or times['scheduled_close_time'])),
                   'content_sources': content_sources(raw_post, originals[ident]),
                   'input_valid': not errors, 'input_errors': errors,
                   'rule_review': issues, 'automatic_candidate': automatic,
                   'review_status': 'manual_rule_review_required' if issues else 'not_semantically_certified',
                   'format_probe_cdf_length': len(probe['continuous_cdf']) if probe else None,
                   'rule_identity': digest({k: question.get(k) for k in
                       ('title', 'resolution_criteria', 'fine_print', 'scaling', 'unit')}),
                   'url': f"https://www.metaculus.com/questions/{post['id']}/"}
            rows.append(row)
            if request is not None and not errors:
                inputs[ident] = {'request': request, 'financial_profile': financial_profile(question),
                                 'request_sha256': digest(request)}
    rows.sort(key=lambda row: (row['conservative_deadline_utc'] or '9999', row['question_id']))
    advertised = metadata.get('questions_count_including_subquestions')
    return {'schema': 'market-pulse-inventory-v1', 'tournament': SLUG,
            'retrieved_at_utc': now.isoformat(), 'baseline_tag': BASELINE_TAG,
            'baseline_commit': BASELINE_COMMIT, 'visible_posts': len(posts),
            'visible_question_groups': sum(bool(p.get('group_of_questions')) for p in posts),
            'visible_leaf_questions': len(rows), 'status_counts': dict(Counter(r['status'] for r in rows)),
            'type_counts': dict(Counter(r['type'] for r in rows)),
            'platform_open_with_permission': sum(r['platform_open_with_permission'] for r in rows),
            'format_valid': sum(r['input_valid'] for r in rows),
            'manual_rule_review_questions': sum(bool(r['rule_review']) for r in rows),
            'automatic_candidates': sum(r['automatic_candidate'] for r in rows),
            'metadata_leaf_count': advertised,
            'metadata_leaves_not_exposed_in_feed': max(0, advertised - len(rows)) if isinstance(advertised, int) else None,
            'unexposed_leaf_status': 'unknown', 'all_inputs_semantically_certified': False,
            'paid_provider_calls': 0, 'forecasts_submitted': 0, 'rows': rows}, inputs


def snapshot(root):
    """Capture official GET responses into a new, immutable directory."""
    import requests
    root = Path(root)
    if root.exists():
        raise ValueError('Snapshot destination must be new')
    token = os.environ.get('METACULUS_TOKEN')
    account = Client(token).account()
    session = requests.Session()
    session.headers.update({'Authorization': 'Token ' + token, 'Accept': 'application/json',
                            'User-Agent': 'ForecastAgent-MarketPulse-readonly/1.0'})
    root.mkdir(parents=True)
    paths = {'/api/posts/', f'/api/projects/tournaments/{SLUG}/'}

    def get(url, params=None):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'www.metaculus.com' or parsed.path not in paths:
            raise ValueError('Unexpected authenticated API destination')
        if parsed.path == '/api/posts/':
            scope = (params or {}).get('tournaments') if params else parse_qs(parsed.query).get('tournaments', [])
            if scope not in (SLUG, [SLUG]):
                raise ValueError('Pagination escaped the tournament scope')
        response = session.get(url, params=params, timeout=45, allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError(f'Official API HTTP {response.status_code} at {parsed.path}')
        return response.json()

    try:
        metadata = get(API + f'projects/tournaments/{SLUG}/')
        save(root / 'metadata.json', metadata)
        posts, pages = {}, []
        url, params = API + 'posts/', {'tournaments': SLUG, 'limit': 100,
                                     'include_descriptions': 'true', 'with_cp': 'false'}
        for _ in range(30):
            page = get(url, params)
            pages.append(page)
            save(root / 'feed-pages.json', pages)
            for post in page['results']:
                posts[post['id']] = post
            if not page['results'] or not page.get('next'):
                break
            url, params = page['next'], None
        else:
            raise RuntimeError('Feed pagination incomplete')
        for ident, post in list(posts.items()):
            if questions(post):
                path = f'/api/posts/{ident}/'
                paths.add(path)
                detail = get('https://www.metaculus.com' + path,
                             {'include_descriptions': 'true', 'with_cp': 'false'})
                if detail.get('id') != ident:
                    raise ValueError('Post detail identity mismatch')
                posts[ident] = detail
        save(root / 'posts.json', list(posts.values()))
        now = datetime.now(timezone.utc).isoformat()
        report, inputs = inspect(metadata, list(posts.values()), now)
        report['account'] = account
        report['visible_feed_pagination_complete'] = True
        save(root / 'inventory.json', report)
        for ident, document in inputs.items():
            save(root / 'inputs' / f'{ident}.json', document)
        fields = ['question_id', 'post_id', 'label', 'type', 'unit', 'status',
                  'platform_open_with_permission', 'input_valid', 'automatic_candidate',
                  'conservative_deadline_utc', 'url']
        with (root / 'questions.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(report['rows'])
        files = [p for p in sorted(root.rglob('*')) if p.is_file()]
        import hashlib
        save(root / 'checksums.json', {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
        return report
    except Exception as exc:
        save(root / 'failure.json', {'error': f'{type(exc).__name__}: {exc}',
                                     'preserved_partial_snapshot': True, 'forecasts_submitted': 0})
        raise
    finally:
        session.close()
