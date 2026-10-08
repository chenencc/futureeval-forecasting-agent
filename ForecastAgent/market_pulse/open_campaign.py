"""User-authorized open-question campaign over unchanged financial engines."""
import argparse
import copy
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.parse import urljoin

if os.environ.get('FORECAST_PROVIDER_FAILOVER_MODULE'):
    import runpy
    runpy.run_path(os.environ['FORECAST_PROVIDER_FAILOVER_MODULE'])['install']()

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.competition.platform import has_existing_forecast, deliver, forecast_matches
from ForecastAgent.competition.queue import questions
from ForecastAgent.market_pulse import inventory, analysis
from ForecastAgent.market_pulse.manual_delivery import PacedClient, metadata, compare, format_quantile
from ForecastAgent.runtime.task_lock import task_lock

VERSION = 'market-pulse-open-campaign-v1'


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def scoped_feed_path(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'www.metaculus.com' or parsed.path != '/api/posts/':
        raise ValueError('Official pagination destination changed')
    scope = parse_qs(parsed.query).get('tournaments')
    if scope not in ([inventory.SLUG], [str(inventory.PROJECT_ID)]):
        raise ValueError('Pagination escaped authorized tournament')
    return 'posts/?' + parsed.query


def capture(root):
    """Freeze all visible leaves and authenticated own-history before any spend."""
    root.mkdir(parents=True, exist_ok=True)
    folder = root / 'official'
    with task_lock(root):
        if (folder / 'checksums.json').exists():
            for name, expected in load(folder / 'checksums.json').items():
                path = (folder / name).resolve()
                if not path.is_relative_to(folder.resolve()) or analysis.sha(path) != expected:
                    raise ValueError('Frozen official snapshot changed')
            return load(folder / 'campaign-inventory.json')
        if folder.exists():
            raise ValueError('Partial official snapshot exists; inspect preserved state')
        folder.mkdir()
        client = PacedClient(os.environ['METACULUS_TOKEN'], folder / 'read-backoff')
        try:
            account = client.account(); save(folder / 'account.json', account)
            meta = metadata(client); save(folder / 'metadata.json', meta)
            path = 'posts/?' + urlencode({'tournaments': inventory.PROJECT_ID, 'limit': 100,
                'include_descriptions': 'true', 'with_cp': 'true'})
            posts = {}; pages = []
            for _ in range(30):
                page = client.request('GET', path)['data']
                if not isinstance(page, dict) or not isinstance(page.get('results'), list):
                    raise ValueError('Unexpected tournament feed shape')
                pages.append(page); save(folder / 'feed-pages.json', pages)
                for post in page['results']:
                    if questions(post):
                        posts[post['id']] = post
                if not page['results'] or not page.get('next'):
                    break
                path = scoped_feed_path(page['next'])
            else:
                raise ValueError('Tournament feed pagination incomplete')
            for ident in sorted(posts):
                post = client.post(ident)
                if post['id'] != ident:
                    raise ValueError('Post identity changed')
                posts[ident] = post
                save(folder / f'post-{ident}.json', post)
            save(folder / 'posts.json', list(posts.values()))
            report, inputs = inventory.inspect(meta, list(posts.values()), timestamp())
            for ident, packet in inputs.items():
                save(folder / 'inputs' / f'{ident}.json', packet)
            rows = []
            for row in report['rows']:
                post = posts[row['post_id']]
                question = next(q for q in questions(post) if str(q['id']) == row['question_id'])
                own = has_existing_forecast(question)
                state = ('already_forecast' if own else 'not_open' if row['status'] != 'open'
                    else 'rule_review' if row['rule_review'] else 'not_eligible'
                    if not row['automatic_candidate'] else 'pending')
                rows.append({**row, 'campaign_state': state, 'own_forecast_present': own})
            save(folder / 'inventory.json', report)
            campaign = {'schema': VERSION, 'captured_at_utc': timestamp(), 'account': account,
                'tournament': inventory.SLUG, 'visible_feed_pagination_complete': True,
                'visible_leaf_count': len(rows), 'status_counts': report['status_counts'],
                'state_counts': dict(Counter(r['campaign_state'] for r in rows)),
                'rows': rows, 'new_provider_calls': 0, 'submitted': False}
            save(folder / 'campaign-inventory.json', campaign)
            save(folder / 'checksums.json', {p.relative_to(folder).as_posix(): analysis.sha(p)
                for p in sorted(folder.rglob('*')) if p.is_file()})
            print(json.dumps({k: campaign[k] for k in ('visible_leaf_count', 'status_counts', 'state_counts')}, ensure_ascii=False), flush=True)
            return campaign
        except Exception as exc:
            save(folder / 'failure.json', {'at_utc': timestamp(), 'error_type': type(exc).__name__,
                'error': str(exc), 'state_preserved': True, 'submitted': False})
            raise


def collect_new_issuers(root):
    """Collect new issuer tasks once; existing task ledgers are never reset."""
    from ForecastAgent.market_pulse import pilot, collection
    report = capture(root)
    # The last completed pilot contains old NVIDIA reports only. Its EPS sibling
    # is a new task and receives its own original budget, shared by consumers.
    ids = ('46179', '46183', '46199')
    selected = [r for r in report['rows'] if r['question_id'] in ids and r['campaign_state'] == 'pending']
    stage = root / 'acquisition'
    rows = []
    for row in selected:
        ident = row['question_id']; relative = f'inputs/{ident}.json'
        request = collection.prepare(load(root / 'official/inputs' / f'{ident}.json')['request'])
        path = stage / relative
        if path.exists() and load(path) != request:
            raise ValueError('Frozen acquisition request changed')
        save(path, request)
        rows.append({'id': ident, 'post_id': row['post_id'], 'input': relative,
            'request_sha256': analysis.sha(path), 'new_task_not_budget_reset': True})
    manifest = {'version': VERSION, 'rows': rows, 'workers': 2,
        'tavily_basic_lifetime_max': 3, 'exa_lifetime_max': 1,
        'model': pilot.MODEL, 'old_budget_resets': 0, 'submitted': False}
    if (stage / 'manifest.json').exists() and load(stage / 'manifest.json') != manifest:
        raise ValueError('Frozen acquisition campaign changed')
    save(stage / 'manifest.json', manifest)
    with task_lock(stage):
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {pool.submit(pilot.case, stage, row): row['id'] for row in rows}
            for job in as_completed(jobs):
                result = job.result()
                print(json.dumps({'collection_id': jobs[job], 'status': result['status'],
                    'readable_pages': result.get('readable_pages')}), flush=True)
        save(stage / 'report.json', {'finished_at_utc': timestamp(), 'rows':
            [{**row, **load(stage / 'tasks' / row['id'] / 'collection-result.json')} for row in rows],
            'old_budget_resets': 0, 'submitted': False})


def campaign_rows(root):
    report = capture(root); rows = []
    for original in report['rows']:
        row = copy.deepcopy(original)
        codes = {v['code'] for v in row['rule_review']}
        if row['campaign_state'] == 'rule_review' and codes == {'issuer_release_after_close'}:
            # The expected earnings date may legitimately follow a forecasting
            # deadline. Preserve that official deadline and first-release rule.
            review = {'question_id': row['question_id'], 'rule_identity': row['rule_identity'],
                'reviewed_codes': sorted(codes), 'at_utc': timestamp(),
                'interpretation': 'Forecast closes before expected earnings publication. '
                    'This does not change the explicit target quarter or first-release quantity. '
                    'Use the original earliest close/spot time; never extend the deadline.',
                'original_rules_rewritten': False, 'decision': 'eligible_before_original_deadline'}
            path = root / 'rule-reviews' / f'{row["question_id"]}.json'
            if not path.exists():
                save(path, review)
            row['campaign_state'] = 'pending'
            row['manual_timing_review'] = str(path)
        if row['campaign_state'] == 'pending':
            rows.append(row)
    return rows


def shared_package(root, row):
    """Reuse issuer-identical captured bodies, never forecasts or summaries."""
    from ForecastAgent.market_pulse import financial
    ident = row['question_id']; folder = root / 'tasks' / ident
    output = folder / 'acquisition-package.json'
    if output.exists():
        return output
    request = load(root / 'official/inputs' / f'{ident}.json')['request']
    issuer = analysis.contract(request)['issuer']
    acquisition = load(root / 'acquisition/manifest.json') if (root / 'acquisition/manifest.json').exists() else {'rows': []}
    for owner in acquisition['rows']:
        own_request = load(root / 'acquisition' / owner['input'])
        if analysis.contract(own_request)['issuer'] == issuer and not (
                root / 'acquisition/tasks' / owner['id'] / 'collection-result.json').exists():
            return None
    data_root = root.parent.parent
    paths = list((data_root / 'pilots').glob('*/tasks/*/retrieval/release-1.0.5/package.json'))
    paths += list((data_root / 'supplements').glob('**/enriched-package.json'))
    paths += list((root / 'acquisition/tasks').glob('*/retrieval/release-1.0.5/package.json'))
    parents = []; pages = {}; owners = {}; superseded = []
    for path in sorted(set(paths)):
        original = load(path)
        if financial.issuer_profile(original.get('request', {}))['issuer_label'] != issuer:
            continue
        parent = {'path': str(path), 'sha256': analysis.sha(path),
            'owner_question_id': str(original['request']['id']), 'original_capture_budget_reset': False}
        parents.append(parent)
        for url, page in original.get('pages', {}).items():
            if url in pages and digest(pages[url]) != digest(page):
                old_time = pages[url].get('retrieved_at_utc', '')
                new_time = page.get('retrieved_at_utc', '')
                if new_time < old_time:
                    superseded.append({'url': url, 'source': str(path), 'retained_later_capture': True})
                    continue
                superseded.append({'url': url, 'source': owners[url]['path'], 'retained_later_capture': True})
            pages[url] = copy.deepcopy(page); owners[url] = parent
    if not parents:
        return None
    bundle = {'request': request, 'pages': pages,
        'gaps': ['Shared saved issuer material is not a completeness or factual-verification certificate.',
                 'Future actual release is not required; missing current guidance/estimates remain uncertainty.'],
        'capture_parents': parents, 'per_page_capture_owner': owners,
        'superseded_saved_versions': superseded, 'created_at_utc': timestamp(),
        'original_own_search_calls': 0, 'original_capture_files_unchanged': True,
        'shared_original_bodies_only': True}
    view, excluded = analysis.analysis_view(bundle)
    view['excluded_original_pages'] = excluded
    if not view['pages']:
        return None
    save(output, view); save(folder / 'capture-provenance.json', {'parents': parents,
        'excluded_pages': excluded, 'source_cost_charged_to_original_owner': True,
        'new_tavily_search_calls': 0, 'new_exa_search_calls': 0, 'budget_resets': 0})
    return output


def analyze_pending(root, ids=None):
    """Run the independent financial engine without platform/search keys."""
    (root / 'analysis-worker').mkdir(parents=True, exist_ok=True)
    with task_lock(root / 'analysis-worker'):
        rows = campaign_rows(root)
        if ids is not None:
            if not set(ids) <= {r['question_id'] for r in rows}:
                raise ValueError('Selected analysis IDs are not pending eligible leaves')
            rows = [r for r in rows if r['question_id'] in ids]
        for row in rows:
            ident = row['question_id']; folder = root / 'tasks' / ident / 'analysis'
            if (folder / 'result.json').exists():
                continue
            source = shared_package(root, row)
            if source is None:
                save(root / 'tasks' / ident / 'pending-materials.json', {
                    'state': 'awaiting_materials', 'at_utc': timestamp(), 'submitted': False})
                print(json.dumps({'id': ident, 'status': 'awaiting_materials'}), flush=True)
                continue
            folder.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8='1')
            for key in ('METACULUS_TOKEN', 'TAVILY_API_KEY', 'TAVILY_API_KEY2', 'EXA_API_KEY', 'EXA_API_KEY2'):
                env.pop(key, None)
            save(root / 'analysis-progress.json', {'active_id': ident, 'at_utc': timestamp()})
            with (folder / 'stdout.log').open('a', encoding='utf-8') as out, (folder / 'stderr.log').open('a', encoding='utf-8') as err:
                execution = subprocess.run([sys.executable, '-X', 'utf8', '-u', '-m',
                    'ForecastAgent.market_pulse.batch_analysis', '--package', str(source),
                    '--root', str(folder)], env=env, stdout=out, stderr=err)
            if not (folder / 'result.json').exists():
                save(folder / 'process-failure.json', {'returncode': execution.returncode,
                    'state_preserved': True, 'submitted': False})
                print(json.dumps({'id': ident, 'status': 'process_failed'}), flush=True)
            else:
                result = load(folder / 'result.json')
                print(json.dumps({k: result.get(k) for k in ('id', 'status', 'quantiles',
                    'manual_delivery_candidate', 'error')}, ensure_ascii=False), flush=True)
        save(root / 'analysis-progress.json', {'state': 'finished_pass', 'at_utc': timestamp()})


def supplement(root, ident):
    """One remaining basic search plus bounded free reads; old snapshots stay."""
    from ForecastAgent.market_pulse import enrichment, financial, derivation
    from ForecastAgent.providers.tavily_search import search_batch
    row = next(r for r in campaign_rows(root) if r['question_id'] == ident)
    source = shared_package(root, row)
    if source is None:
        raise ValueError('An original saved package is required before supplementation')
    folder = root / 'tasks' / ident / 'supplement-v1'; folder.mkdir(parents=True, exist_ok=True)
    with task_lock(folder):
        original = load(source); request = original['request']; profile = financial.issuer_profile(request)
        parent_files = []
        for path in (root.parent.parent / 'pilots').glob(f'*/tasks/{ident}/retrieval/release-1.0.5/collection/bundle.json'):
            parent = load(path)
            parent_files.append({'path': str(path), 'sha256': analysis.sha(path),
                'tavily_basic_calls': len(parent.get('searches', [])), 'exa_calls': len(parent.get('exa_searches', []))})
        previous = max((p['tavily_basic_calls'] for p in parent_files), default=0)
        target = derivation.fiscal_period(analysis.contract(request)['target_period'])
        quarter, year = target
        prior = (quarter - 1, year) if quarter > 1 else (4, year - 1)
        query = (f'{profile["issuer_label"]} Q{prior[0]} FY{prior[1]} Q{quarter} FY{year-1} '
                 'official earnings financial results GAAP diluted EPS quarterly revenue update report PDF')
        identity = {'parent_package': str(source), 'parent_sha256': analysis.sha(source),
            'old_search_ledgers': parent_files, 'previous_basic_calls': previous,
            'new_basic_max': int(previous < 3), 'free_fetch_max': 8,
            'query': query, 'old_budget_resets': 0, 'new_exa_calls': 0}
        if (folder / 'identity.json').exists() and load(folder / 'identity.json') != identity:
            raise ValueError('Frozen supplementary budget or query changed')
        save(folder / 'identity.json', identity)
        search = folder / 'basic-search.json'
        if not search.exists() and previous < 3:
            reservation = folder / 'basic-reservation.json'
            if not reservation.exists():
                save(reservation, {'reserved_at_utc': timestamp(), 'logical_calls': 1,
                    'lifetime_basic_calls_after_reservation': previous + 1, 'old_budget_reset': False})
                try:
                    save(search, search_batch(query, os.environ['TAVILY_API_KEY']))
                except Exception as exc:
                    save(folder / 'basic-failure.json', {'error_type': type(exc).__name__,
                        'error': str(exc), 'reservation_preserved': True})
        leads = []
        if search.exists():
            leads += [hit['url'] for hit in load(search)['results']]
        for url, page in original['pages'].items():
            for match in re.finditer(r'\[[^\]]+\]\(([^)]+)\)', page.get('content', '')):
                leads.append(urljoin(url, match[1]))
        leads = list(dict.fromkeys(u for u in leads if u.startswith('https://')
            and financial.page_scope(u, {}, profile)['eligible_for_target']
            and ('metaculus.com' not in urlsplit(u).netloc)))
        leads.sort(key=lambda u: (not u.lower().endswith('.pdf'),
            not bool(re.search(r'earnings|quarter|results|financial|update|ex-?99', u, re.I))))
        chosen = leads[:8]
        if (folder / 'leads.json').exists() and load(folder / 'leads.json') != chosen:
            raise ValueError('Frozen supplementary page selection changed')
        save(folder / 'leads.json', chosen)
        bundle = copy.deepcopy(original); captures = []
        for index, url in enumerate(chosen):
            capture_folder = folder / 'captures' / f'{index:02}'
            receipt = enrichment.capture(url, capture_folder, 'original_report', timestamp()[:10])
            captures.append(receipt)
            if receipt['status'] == 'received' and (capture_folder / 'page.json').exists():
                page = load(capture_folder / 'page.json')
                if financial.page_scope(url, page, profile)['eligible_for_target']:
                    bundle['pages'][url] = page
        bundle, excluded = analysis.analysis_view(bundle)
        bundle.update(supplement_parent=str(source), supplement_parent_sha256=analysis.sha(source),
            created_at_utc=timestamp(), new_material_is_independent_timestamped_supplement=True)
        destination = folder / 'package.json'
        if destination.exists() and load(destination) != bundle:
            raise ValueError('Completed supplementary package changed')
        save(destination, bundle)
        save(folder / 'report.json', {'captures': captures, 'new_tavily_basic_calls': int((folder / 'basic-reservation.json').exists()),
            'prior_tavily_basic_calls': previous, 'lifetime_tavily_basic_calls': previous + int((folder / 'basic-reservation.json').exists()),
            'new_exa_calls': 0, 'free_fetch_attempts': len(captures), 'excluded_pages': excluded,
            'package': str(destination), 'original_package_unchanged': analysis.sha(source) == identity['parent_sha256'],
            'old_budget_resets': 0, 'submitted': False})
        print(json.dumps({'supplement_id': ident, 'readable_pages': len(bundle['pages']),
            'lifetime_basic_calls': previous + int((folder / 'basic-reservation.json').exists()), 'package': str(destination)}), flush=True)
        return destination


def latest_result(root, ident):
    folders = sorted((p.parent for p in (root / 'tasks' / ident).glob('analysis*/result.json')),
                     key=lambda p: (p / 'result.json').stat().st_mtime_ns)
    results = [(p, load(p / 'result.json')) for p in folders if (p / 'result.json').exists()]
    eligible = [(p, r) for p, r in results if r.get('manual_delivery_candidate')]
    return eligible[-1] if eligible else results[-1] if results else (None, None)


def delivery_entry(root, row, folder, result):
    ident = row['question_id']; saved = root / 'tasks' / ident / 'delivery/plan.json'
    if saved.exists():
        return load(saved)
    package = Path(load(folder / 'identity.json')['package'])
    bundle = load(package); financial = analysis.contract(bundle['request'])
    sources = list(dict.fromkeys(v['original_row_ref']['url'] for v in result.get('variables', [])
        if not v['original_row_ref'].get('field')))
    if not sources:
        sources = list(bundle['pages'])
    qs = result['quantiles']
    comment = ('# ForecastAgent — Market Pulse 26Q4\n\n'
        f'## Target\n{financial["issuer"]}: {financial["metric"]}, {financial["target_period"]}. '
        'Use the first official release and original question rules.\n\n'
        '## Predictive distribution\n' + '\n'.join(f'- {name}: '
            + format_quantile(qs[p], financial['metric'], bundle['request'])
            for name, p in [('10th percentile', '0.1'), ('Median', '0.5'), ('90th percentile', '0.9')])
        + '\n\n## Method and limitations\n'
        'Saved original issuer financial evidence is kept distinct from source interpretations. '
        'Numeric token/unit bindings and source compatibility are checked before scoring. '
        'The Mercury outcome distribution reads original evidence independently of any Super '
        'projection; no analyst forecast or program midpoint is averaged into this submission. '
        'Quarterly versus annual/YTD columns, fiscal versus calendar periods, GAAP versus '
        'adjusted EPS, currency units, shares and one-off disclosures remain explicit. '
        'Missing current guidance/consensus, future margins, taxes, expense recurrence and '
        'share counts create forecast uncertainty. Source approvals are diagnostics, not '
        'truth certificates; probability calibration and prospective accuracy remain unvalidated.\n\n'
        '## Original sources\n' + '\n'.join('- ' + url for url in sources)
        + '\n\nFull CDF submitted with open tails. CDF probabilities are bounded to '
        '[0.02, 0.98]; financial values are not clipped. Forecast is conditional on '
        'valid numeric resolution. Annulment is a separate administrative outcome.')
    entry = {'id': ident, 'post_id': row['post_id'], 'package': str(package),
        'package_sha256': analysis.sha(package), 'audit_path': str(folder / 'result.json'),
        'audit_sha256': analysis.sha(folder / 'result.json'), 'payload_path': str(folder / 'result.json'),
        'payload_file_sha256': analysis.sha(folder / 'result.json'), 'candidate': result['payload'],
        'candidate_sha256': digest(result['payload']), 'comment': comment,
        'issuer': financial['issuer'], 'metric': financial['metric'],
        'target_period': financial['target_period'], 'quantiles': qs,
        'source_method': VERSION, 'user_authorized_open_tournament_submission': True}
    save(saved, entry)
    return entry


def live_delivery_check(root, entry, meta, post):
    inspected, inputs = inventory.inspect(meta, [post], timestamp())
    row = next(r for r in inspected['rows'] if r['question_id'] == entry['id'])
    if row['rule_review']:
        audit_path = root / 'rule-reviews' / f'{entry["id"]}.json'
        audit = load(audit_path) if audit_path.exists() else {}
        codes = sorted({v['code'] for v in row['rule_review']})
        if (codes == ['issuer_release_after_close'] and audit.get('reviewed_codes') == codes
                and audit.get('rule_identity') == row['rule_identity']
                and row['platform_open_with_permission']
                and inventory.instant(row['conservative_deadline_utc']) > inventory.instant(timestamp())):
            row = {**row, 'rule_review': [], 'automatic_candidate': True}
    question = next(q for q in questions(post) if str(q['id']) == entry['id'])
    return compare(entry, inputs[entry['id']]['request'], row, question)


def submit_ready(root):
    """Bounded, receipt-safe authorized submissions; no forecast recalculation."""
    worker = root / 'delivery-worker'; worker.mkdir(parents=True, exist_ok=True)
    with task_lock(worker):
        client = PacedClient(os.environ['METACULUS_TOKEN'], root / 'delivery-read-backoff')
        account = client.account(); meta = metadata(client)
        if meta.get('bot_leaderboard_status') != 'include' or 'Bot Participation Rules' not in meta.get('description', ''):
            raise ValueError('Bot tournament participation rules changed')
        selected = []
        for row in campaign_rows(root):
            if (root / 'tasks' / row['question_id'] / 'hold.json').exists():
                continue
            folder, result = latest_result(root, row['question_id'])
            if result and result.get('manual_delivery_candidate'):
                selected.append(delivery_entry(root, row, folder, result))
        parents = {e['post_id'] for e in selected}
        posts = {p: client.post(p) for p in sorted(parents)}
        preflight = []
        for entry in selected:
            preflight.append(live_delivery_check(root, entry, meta, posts[entry['post_id']]))
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')
        preflight_path = worker / stamp
        save(preflight_path / 'preflight.json', {'account': account, 'rows': preflight,
            'all_selected_rules_and_formats_valid': True, 'selected_ids': [e['id'] for e in selected]})
        for p, post in posts.items():
            save(preflight_path / f'initial-post-{p}.json', post)
        receipts = []
        for entry in selected:
            folder = root / 'tasks' / entry['id'] / 'delivery'
            try:
                post = client.post(entry['post_id'])
                check = live_delivery_check(root, entry, meta, post)
                if check['existing_forecast'] and not (folder / 'submission.json').exists():
                    receipt = {'status': 'existing_forecast_not_overwritten', 'submitted': False}
                else:
                    receipt = deliver(client, {'id': entry['id'], 'post_id': entry['post_id'],
                        'analysis_release_version': VERSION}, entry['candidate'], entry['comment'], folder, enabled=True)
                record = {'id': entry['id'], 'issuer': entry['issuer'], 'receipt': receipt,
                    'quantiles': entry['quantiles']}
            except Exception as exc:
                record = {'id': entry['id'], 'status': 'needs_review', 'error_type': type(exc).__name__,
                    'error': str(exc), 'state_preserved': True, 'no_blind_duplicate_post': True}
            receipts.append(record); save(worker / 'progress.json', {'results': receipts})
            print(json.dumps({'delivery_id': entry['id'], 'status':
                record.get('receipt', {}).get('status', record.get('status'))}), flush=True)
        final = {p: client.post(p) for p in sorted(parents)}
        verified = []
        for entry in selected:
            post = final[entry['post_id']]
            question = next(q for q in questions(post) if str(q['id']) == entry['id'])
            verified.append({'id': entry['id'], 'matches_authenticated_platform': forecast_matches(question, entry['candidate'])})
        for p, post in final.items():
            save(preflight_path / f'final-post-{p}.json', post)
        report = {'schema': VERSION, 'finished_at_utc': timestamp(), 'account': account,
            'results': receipts, 'readback': verified, 'new_model_calls': 0,
            'confirmed_matching_forecasts': sum(v['matches_authenticated_platform'] for v in verified),
            'confirmed_receipts': sum(r.get('receipt', {}).get('status') == 'accepted' for r in receipts),
            'old_four_forecasts_not_reposted': True, 'production_listener_changed': False}
        save(preflight_path / 'report.json', report); save(worker / 'report.json', report)
        print(json.dumps({'confirmed_additional_forecasts': report['confirmed_matching_forecasts']}), flush=True)


def recover_pending(root, ids):
    """Preserve original failures; recover packing or one bounded fact correction."""
    worker = root / 'analysis-recovery-worker'; worker.mkdir(parents=True, exist_ok=True)
    with task_lock(worker):
        for ident in ids:
            folder = root / 'tasks' / ident / 'analysis-recovery-v1'
            old = root / 'tasks' / ident / 'analysis'
            if (folder / 'result.json').exists():
                first = load(folder / 'result.json')
                if first.get('manual_delivery_candidate'):
                    continue
                if (first.get('error') == 'Source review has no dated facts or exceeds bounded context'
                        and not list(folder.glob('**/http/*.json'))
                        and (old / 'facts/variables.json').exists()):
                    folder = root / 'tasks' / ident / 'analysis-recovery-v2'
                    if (folder / 'result.json').exists():
                        continue
                else:
                    continue
            prior = load(old / 'result.json')
            if prior.get('manual_delivery_candidate'):
                raise ValueError('Recovery may not rerun a usable forecast')
            source = root / 'tasks' / ident / 'acquisition-package.json'
            args = [sys.executable, '-X', 'utf8', '-u', '-m', 'ForecastAgent.market_pulse.batch_analysis',
                    '--package', str(source), '--root', str(folder)]
            cached = old / 'facts/variables.json'
            if cached.exists():
                args += ['--reuse-facts', str(cached)]
            else:
                args += ['--repair-from', str(old)]
            folder.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8='1')
            for key in ('METACULUS_TOKEN', 'TAVILY_API_KEY', 'TAVILY_API_KEY2', 'EXA_API_KEY', 'EXA_API_KEY2'):
                env.pop(key, None)
            with (folder / 'stdout.log').open('a', encoding='utf-8') as out, (folder / 'stderr.log').open('a', encoding='utf-8') as err:
                subprocess.run(args, env=env, stdout=out, stderr=err)
            result = load(folder / 'result.json')
            print(json.dumps({k: result.get(k) for k in ('id', 'status', 'quantiles',
                'manual_delivery_candidate', 'error')}, ensure_ascii=False), flush=True)


def rescore_units(root, ident):
    """One independent, source-preserving units reread; never rewrite a forecast."""
    from ForecastAgent.market_pulse import compact_trial as compact
    from ForecastAgent.analysis import mercury_nonbinary_trial as typed
    from ForecastAgent.analysis.distributions import payload
    from ForecastAgent.competition.mercury import distribution_spec
    from ForecastAgent.releases.v1_0_5 import validate_payload
    old = root / 'tasks' / ident / 'analysis'
    folder = root / 'tasks' / ident / 'analysis-units-v1'; folder.mkdir(parents=True, exist_ok=True)
    with task_lock(folder):
        if (folder / 'result.json').exists():
            return load(folder / 'result.json')
        original = load(old / 'result.json')
        source = root / 'tasks' / ident / 'acquisition-package.json'; bundle = load(source)
        state = load(old / 'source-state.json')
        state['unit_review'] = ('Original normalized quantities are in RAW USD, not millions or billions. '
            'Divide by 1,000,000,000 only when describing billions; never change the raw-USD value. '
            'The platform grid is not a forecast or support restriction. The report can fall far '
            'above its upper cutpoint; assign the corresponding upper-open-tail probability. '
            'Do not anchor a forecast inside the grid. Re-read actual revenue rows and their quoted '
            'units, quarter labels and original evidence. No previous forecast is supplied.')
        spec = distribution_spec(bundle['request']); registry = compact.outcome_questions(spec)
        if analysis.chain.request_bytes(state, registry) > compact.POLICY['request_byte_limit']:
            raise ValueError('Source-preserving unit reread exceeds bounded context')
        save(folder / 'identity.json', {'package': str(source), 'package_sha256': analysis.sha(source),
            'parent_result': str(old / 'result.json'), 'parent_sha256': analysis.sha(old / 'result.json'),
            'new_scoring_attempt_cap': 1, 'old_attempts_and_candidate_preserved': True})
        response = analysis.chain.call(state, folder / 'mercury', registry)
        forecast = typed.forecast(response, spec)
        candidate = payload(bundle['request'], {'continuous_cdf': forecast['raw_cdf']})
        validate_payload(bundle['request'], candidate)
        qs = {str(p): analysis.quantile(candidate['continuous_cdf'], bundle['request'], p) for p in (.1, .5, .9)}
        coherent = qs['0.5']['state'] == 'above_platform_range'
        result = {**original, 'payload': candidate, 'quantiles': qs, 'raw_distribution': forecast,
            'unit_reread_preserves_original_source_exposure': True,
            'manual_delivery_candidate': coherent, 'original_candidate_not_rewritten': True,
            'usage': analysis.usage_audit([load(p) for p in folder.glob('**/http/*.json')])}
        save(folder / 'result.json', result)
        print(json.dumps({'units_reread_id': ident, 'quantiles': qs, 'unit_review_passed': coherent}), flush=True)
        return result


def salvage_saved_facts(root, ident):
    """Isolate failed original fact selections independently, with zero new generation."""
    from ForecastAgent.market_pulse import numeric_binding, formulas
    old = root / 'tasks' / ident / 'analysis-recovery-v1'
    bundle_path = root / 'tasks' / ident / 'acquisition-package.json'
    bundle = load(bundle_path); table = load(old / 'facts/visible-rows.json')
    journal = sorted((old / 'facts/http').glob('*.json'))[-1]
    message = load(journal)['response']['choices'][0]['message']
    raw = json.loads(message['tool_calls'][0]['function']['arguments'])
    variables = []; rejected = []
    for index, selection in enumerate(raw['facts']):
        try:
            bound, _, audit = numeric_binding.bind(bundle, table, [selection], minimum_facts=1)
            bound[0]['fact_id'] = 'V' + str(len(variables) + 1)
            formulas.validate_variables(bundle, bound)
            variables += bound
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'selection_index': index, 'selection': selection, 'error': str(exc)})
    folder = root / 'tasks' / ident / 'saved-fact-isolation'
    save(folder / 'variables.json', variables)
    save(folder / 'audit.json', {'source_journal': str(journal), 'source_sha256': analysis.sha(journal),
        'accepted': len(variables), 'rejected': rejected, 'new_model_calls': 0,
        'original_responses_and_numbers_not_rewritten': True, 'semantic_verification': False})
    if not variables:
        raise ValueError('No saved fact survives independent literal/unit binding; audit preserved')
    destination = root / 'tasks' / ident / 'analysis-salvage-v1'
    destination.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8='1')
    for key in ('METACULUS_TOKEN', 'TAVILY_API_KEY', 'TAVILY_API_KEY2', 'EXA_API_KEY', 'EXA_API_KEY2'):
        env.pop(key, None)
    with (destination / 'stdout.log').open('a', encoding='utf-8') as out, (destination / 'stderr.log').open('a', encoding='utf-8') as err:
        subprocess.run([sys.executable, '-X', 'utf8', '-u', '-m', 'ForecastAgent.market_pulse.batch_analysis',
            '--package', str(bundle_path), '--root', str(destination),
            '--reuse-facts', str(folder / 'variables.json')], env=env, stdout=out, stderr=err)
    result = load(destination / 'result.json')
    print(json.dumps({'id': ident, 'isolated_valid_facts': len(variables), 'rejected_facts': len(rejected),
        'status': result['status'], 'quantiles': result.get('quantiles'),
        'manual_delivery_candidate': result.get('manual_delivery_candidate'), 'error': result.get('error')}), flush=True)


def rescue_supplement(root, ident):
    """Bounded basic Extract rescue and observed report-link reads, not another search."""
    import hashlib
    from ForecastAgent.market_pulse import enrichment, financial, quality
    from ForecastAgent.providers.tavily_extract import extract_basic
    source = root / 'tasks' / ident / 'supplement-v1/package.json'
    parent = load(source); profile = financial.issuer_profile(parent['request'])
    stage = root / 'tasks' / ident / 'supplement-rescue-v1'; stage.mkdir(parents=True, exist_ok=True)
    with task_lock(stage):
        output = stage / 'package.json'
        if output.exists():
            return output
        captures = load(source.parent / 'report.json')['captures']
        urls = [r['url'] for r in captures if r['status'] == 'failed'
            and re.search(r'financial-results|earnings|results', r['url'], re.I)][:3]
        if not urls:
            raise ValueError('No important failed financial-report page to rescue')
        reservation = stage / 'extract-reservation.json'
        response_path = stage / 'extract-response.json'
        if not response_path.exists():
            if reservation.exists():
                raise ValueError('Unresolved basic Extract reservation; do not silently repeat')
            save(reservation, {'urls': urls, 'depth': 'basic', 'new_basic_search_calls': 0,
                'new_extract_batches': 1, 'reserved_at_utc': timestamp()})
            save(response_path, extract_basic(urls, os.environ['TAVILY_API_KEY']))
        response = load(response_path); bundle = copy.deepcopy(parent); leads = []
        for item in response.get('results', []):
            text = item.get('raw_content', ''); url = item['url']
            page = {'url': url, 'content': text, 'retrieved_at_utc': timestamp(), 'documents': [],
                'sha256': hashlib.sha256(text.encode()).hexdigest(), 'capture_method': 'tavily_basic_extract',
                'body_diagnostics': quality.diagnostics(text), 'raw_response_sha256': analysis.sha(response_path)}
            if page['body_diagnostics']['usable_text']:
                bundle['pages'][url] = page
            for match in re.finditer(r'\[[^\]]+\]\(([^)]+)\)', text):
                lead = urljoin(url, match[1])
                if lead.startswith('https://') and (lead.lower().endswith('.pdf') or
                        re.search(r'update|quarter|earnings|results|ex-?99', lead, re.I)):
                    leads.append(lead)
        leads = list(dict.fromkeys(u for u in leads if financial.page_scope(u, {}, profile)['eligible_for_target']))[:4]
        save(stage / 'observed-report-leads.json', leads)
        receipts = []
        for index, url in enumerate(leads):
            folder = stage / 'captures' / f'{index:02}'
            receipt = enrichment.capture(url, folder, 'original_report', timestamp()[:10])
            receipts.append(receipt)
            if receipt['status'] == 'received' and (folder / 'page.json').exists():
                bundle['pages'][url] = load(folder / 'page.json')
        bundle, excluded = analysis.analysis_view(bundle)
        bundle.update(supplement_parent=str(source), supplement_parent_sha256=analysis.sha(source),
            created_at_utc=timestamp(), original_capture_files_unchanged=True)
        save(output, bundle); save(stage / 'report.json', {'new_basic_search_calls': 0,
            'new_extract_batches': 1, 'extract_usage': response.get('usage'),
            'observed_link_fetches': receipts, 'excluded_pages': excluded, 'old_budget_resets': 0,
            'readable_pages': len(bundle['pages']), 'package': str(output)})
        print(json.dumps({'rescued_owner': ident, 'readable_pages': len(bundle['pages']),
            'new_search_calls': 0, 'extract_usage': response.get('usage')}), flush=True)
        return output


def analyze_supplement(root, ids):
    """EPS/revenue siblings reference one separately charged issuer supplement."""
    owner = ids[0]; shared = root / 'tasks' / owner / 'supplement-rescue-v1/package.json'
    original = load(shared)
    for ident in ids:
        source = root / 'tasks' / ident / 'supplement-analysis-package.json'
        bundle = copy.deepcopy(original)
        request = load(root / 'official/inputs' / f'{ident}.json')['request']
        if analysis.contract(request)['issuer'] != analysis.contract(original['request'])['issuer']:
            raise ValueError('Supplement cannot cross issuer identity')
        bundle['request'] = request
        bundle['shared_supplement_owner'] = owner
        if source.exists() and load(source) != bundle:
            raise ValueError('Frozen supplementary analysis package changed')
        save(source, bundle)
        folder = root / 'tasks' / ident / 'analysis-supplement-v1'; folder.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8='1')
        for key in ('METACULUS_TOKEN', 'TAVILY_API_KEY', 'TAVILY_API_KEY2', 'EXA_API_KEY', 'EXA_API_KEY2'):
            env.pop(key, None)
        with (folder / 'stdout.log').open('a', encoding='utf-8') as out, (folder / 'stderr.log').open('a', encoding='utf-8') as err:
            subprocess.run([sys.executable, '-X', 'utf8', '-u', '-m', 'ForecastAgent.market_pulse.batch_analysis',
                '--package', str(source), '--root', str(folder)], env=env, stdout=out, stderr=err)
        result = load(folder / 'result.json')
        print(json.dumps({k: result.get(k) for k in ('id', 'status', 'quantiles',
            'manual_delivery_candidate', 'error')}, ensure_ascii=False), flush=True)


def original_report_index(root, owner):
    """Read one observed issuer index and up to four observed original PDF links."""
    import hashlib
    from ForecastAgent.market_pulse import enrichment, financial, quality
    from ForecastAgent.providers.tavily_extract import extract_basic
    parent_path = root / 'tasks' / owner / 'supplement-rescue-v1/package.json'
    parent = load(parent_path)
    profile = financial.issuer_profile(parent['request'])
    index_url = 'https://ir.tesla.com'
    # This address must occur in already saved original issuer material.
    if not any(index_url in p['content'] for p in parent['pages'].values()):
        raise ValueError('Issuer report index has not been observed in saved material')
    stage = root / 'tasks' / owner / 'report-index-v1'; stage.mkdir(parents=True, exist_ok=True)
    with task_lock(stage):
        if (stage / 'package.json').exists():
            return stage / 'package.json'
        response_path = stage / 'extract-response.json'
        if not response_path.exists():
            reservation = stage / 'extract-reservation.json'
            if reservation.exists():
                raise ValueError('Unresolved index Extract reservation; inspect before retry')
            save(reservation, {'index_url': index_url, 'observed_parent': str(parent_path),
                'parent_sha256': analysis.sha(parent_path), 'new_search_calls': 0,
                'new_extract_batches': 1, 'free_observed_pdf_cap': 4, 'old_budget_resets': 0,
                'reserved_at_utc': timestamp()})
            save(response_path, extract_basic([index_url], os.environ['TAVILY_API_KEY']))
        response = load(response_path); bundle = copy.deepcopy(parent); leads = []
        for item in response.get('results', []):
            text = item.get('raw_content', ''); url = item['url']
            if quality.diagnostics(text)['usable_text']:
                bundle['pages'][url] = {'url': url, 'content': text, 'documents': [],
                    'retrieved_at_utc': timestamp(), 'capture_method': 'tavily_basic_extract',
                    'sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'body_diagnostics': quality.diagnostics(text)}
            for match in re.finditer(r'https://[^\s<>\)\]\"\']+\.pdf(?:\?[^\s<>\)\]\"\']*)?', text):
                lead = match[0]
                if financial.page_scope(lead, {}, profile)['eligible_for_target']:
                    leads.append(lead)
        leads = list(dict.fromkeys(leads))
        # Only ordering is inferred; file paths always come from exact observed links.
        leads.sort(key=lambda u: (not bool(re.search(r'2026|q2', u, re.I)),
            not bool(re.search(r'update|earnings|quarter', u, re.I))))
        chosen = leads[:4]; save(stage / 'observed-pdf-links.json', chosen)
        captures = []
        for index, url in enumerate(chosen):
            folder = stage / 'captures' / f'{index:02}'
            receipt = enrichment.capture(url, folder, 'original_report', timestamp()[:10])
            captures.append(receipt)
            if receipt['status'] == 'received' and (folder / 'page.json').exists():
                bundle['pages'][url] = load(folder / 'page.json')
        bundle, excluded = analysis.analysis_view(bundle)
        bundle.update(supplement_parent=str(parent_path), supplement_parent_sha256=analysis.sha(parent_path),
            created_at_utc=timestamp(), original_capture_files_unchanged=True)
        save(stage / 'package.json', bundle)
        save(stage / 'report.json', {'new_search_calls': 0, 'extract_usage': response.get('usage'),
            'new_extract_batches': 1, 'observed_pdf_captures': captures, 'excluded_pages': excluded,
            'old_budget_resets': 0, 'readable_pages': len(bundle['pages'])})
        print(json.dumps({'report_index_owner': owner, 'observed_pdfs': chosen,
            'received_pdfs': sum(r['status'] == 'received' for r in captures),
            'extract_usage': response.get('usage')}), flush=True)
        return stage / 'package.json'


def analyze_target_rows(root, ids, *, shared_index=False):
    """One explicit source-selection correction; preserve all previous attempts."""
    owner = ids[0]
    for ident in ids:
        if shared_index:
            original_path = root / 'tasks' / owner / 'report-index-v1/package.json'
            rescued = root / 'tasks' / owner / 'report-pdf-rescue-v1/package.json'
            if rescued.exists():
                original_path = rescued
            bundle = load(original_path)
            request = load(root / 'official/inputs' / f'{ident}.json')['request']
            if analysis.contract(request)['issuer'] != analysis.contract(bundle['request'])['issuer']:
                raise ValueError('Shared original report index crosses issuers')
            bundle['request'] = request
            bundle['shared_supplement_owner'] = owner
            source = root / 'tasks' / ident / 'report-index-analysis-package.json'
            if source.exists() and load(source) != bundle:
                raise ValueError('Frozen index package changed')
            save(source, bundle)
            parent = root / 'tasks' / ident / 'analysis-supplement-v1'
            folder = root / 'tasks' / ident / 'analysis-report-index-v1'
        else:
            source = root / 'tasks' / ident / 'acquisition-package.json'
            parent = root / 'tasks' / ident / 'analysis-recovery-v1'
            folder = root / 'tasks' / ident / 'analysis-target-rows-v1'
        folder.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8='1')
        for key in ('METACULUS_TOKEN', 'TAVILY_API_KEY', 'TAVILY_API_KEY2', 'EXA_API_KEY', 'EXA_API_KEY2'):
            env.pop(key, None)
        with (folder / 'stdout.log').open('a', encoding='utf-8') as out, (folder / 'stderr.log').open('a', encoding='utf-8') as err:
            subprocess.run([sys.executable, '-X', 'utf8', '-u', '-m', 'ForecastAgent.market_pulse.batch_analysis',
                '--package', str(source), '--root', str(folder), '--repair-from', str(parent)],
                env=env, stdout=out, stderr=err)
        result = load(folder / 'result.json')
        print(json.dumps({k: result.get(k) for k in ('id', 'status', 'quantiles',
            'manual_delivery_candidate', 'error')}, ensure_ascii=False), flush=True)


def rescue_report_pdfs(root, owner):
    """Use a single basic Extract batch for important failed observed PDFs."""
    import hashlib
    from ForecastAgent.market_pulse import quality
    from ForecastAgent.providers.tavily_extract import extract_basic
    parent_path = root / 'tasks' / owner / 'report-index-v1/package.json'
    index = load(parent_path.parent / 'report.json')
    urls = [r['url'] for r in index['observed_pdf_captures'] if r['status'] == 'failed'][:3]
    if not urls:
        raise ValueError('No failed original report PDF requires rescue')
    stage = root / 'tasks' / owner / 'report-pdf-rescue-v1'; stage.mkdir(parents=True, exist_ok=True)
    with task_lock(stage):
        if (stage / 'package.json').exists():
            return stage / 'package.json'
        response_path = stage / 'extract-response.json'
        if not response_path.exists():
            reservation = stage / 'extract-reservation.json'
            if reservation.exists():
                raise ValueError('Unknown PDF Extract outcome; do not silently repeat')
            save(reservation, {'urls': urls, 'new_extract_batches': 1, 'depth': 'basic',
                'new_search_calls': 0, 'old_budget_resets': 0, 'reserved_at_utc': timestamp()})
            save(response_path, extract_basic(urls, os.environ['TAVILY_API_KEY']))
        response = load(response_path); bundle = load(parent_path); accepted = []
        for row in response.get('results', []):
            text = row.get('raw_content', '')
            if quality.diagnostics(text)['usable_text']:
                bundle['pages'][row['url']] = {'url': row['url'], 'content': text, 'documents': [],
                    'sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'capture_method': 'tavily_basic_extract_pdf', 'retrieved_at_utc': timestamp(),
                    'body_diagnostics': quality.diagnostics(text)}
                accepted.append(row['url'])
        bundle.update(supplement_parent=str(parent_path), supplement_parent_sha256=analysis.sha(parent_path),
            created_at_utc=timestamp(), original_capture_files_unchanged=True)
        save(stage / 'package.json', bundle)
        save(stage / 'report.json', {'extract_usage': response.get('usage'), 'readable_pdf_urls': accepted,
            'failed_results': response.get('failed_results', []), 'new_search_calls': 0,
            'old_budget_resets': 0, 'parent_sha256': analysis.sha(parent_path)})
        print(json.dumps({'report_pdf_owner': owner, 'readable_pdf_urls': accepted,
            'extract_usage': response.get('usage')}), flush=True)


def recover_saved_report_facts(root, ids):
    """Rebind saved successful selections or isolate invalid rows before retry."""
    from ForecastAgent.market_pulse import formulas, numeric_binding
    for ident in ids:
        parent = root / 'tasks' / ident / 'analysis-report-index-v1'
        source = root / 'tasks' / ident / 'report-index-analysis-package.json'
        bundle = load(source); table = load(parent / 'facts/visible-rows.json')
        cached = parent / 'facts/variables.json'
        stage = root / 'tasks' / ident / 'report-fact-isolation-v1'
        rejected = []
        if cached.exists():
            variables = load(cached)
        else:
            journal = sorted((parent / 'facts/http').glob('*.json'))[-1]
            message = load(journal)['response']['choices'][0]['message']
            raw = json.loads(message['tool_calls'][0]['function']['arguments'])
            variables = []
            for index, selection in enumerate(raw['facts']):
                try:
                    bound, _, _ = numeric_binding.bind(bundle, table, [selection], minimum_facts=1)
                    bound[0]['fact_id'] = 'V' + str(len(variables) + 1)
                    formulas.validate_variables(bundle, bound)
                    variables += bound
                except (ValueError, TypeError, KeyError) as exc:
                    rejected.append({'selection_index': index, 'error': str(exc)})
        save(stage / 'variables.json', variables)
        save(stage / 'audit.json', {'accepted': len(variables), 'rejected': rejected,
            'numeric_binding_implementation_sha256': analysis.sha(numeric_binding.__file__),
            'parent_stage': str(parent), 'old_attempts_preserved': True,
            'new_generation_calls': 0, 'new_basic_search_calls': 0, 'old_budget_resets': 0})
        if not variables:
            print(json.dumps({'id': ident, 'status': 'needs_review', 'error': 'No valid saved financial facts'}))
            continue
        folder = root / 'tasks' / ident / 'analysis-report-recovery-v1'; folder.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8='1')
        for key in ('METACULUS_TOKEN', 'TAVILY_API_KEY', 'TAVILY_API_KEY2', 'EXA_API_KEY', 'EXA_API_KEY2'):
            env.pop(key, None)
        with (folder / 'stdout.log').open('a', encoding='utf-8') as out, (folder / 'stderr.log').open('a', encoding='utf-8') as err:
            subprocess.run([sys.executable, '-X', 'utf8', '-u', '-m', 'ForecastAgent.market_pulse.batch_analysis',
                '--package', str(source), '--root', str(folder), '--reuse-facts', str(stage / 'variables.json')],
                env=env, stdout=out, stderr=err)
        result = load(folder / 'result.json')
        print(json.dumps({k: result.get(k) for k in ('id', 'status', 'quantiles',
            'manual_delivery_candidate', 'error')}, ensure_ascii=False), flush=True)


def repair_saved_currency_tokens(root, ident):
    """Explicitly rebind a saved decimal prefix to its complete original token."""
    from ForecastAgent.market_pulse import facts, formulas, numeric_binding
    task = root / 'tasks' / ident
    if (task / 'delivery/submission.json').exists():
        raise ValueError('Token repair cannot rewrite an accepted forecast')
    source = task / 'report-index-analysis-package.json'; bundle = load(source)
    original = load(task / 'report-fact-isolation-v1/variables.json')
    table = facts.discover(bundle); facts.validate_references(bundle, table)
    variables = []; corrections = []
    for old in original:
        ref = old['original_row_ref']; prior = old['numeric_token']
        rows = [r for r in table['candidates'] if r['url'] == ref['url'] and
                r['document_index'] == ref['document_index'] and
                r['refs'][0]['start'] == ref['start'] and r['refs'][0]['end'] == ref['end']]
        if len(rows) != 1:
            raise ValueError('Original row cannot be uniquely rediscovered')
        row = rows[0]
        numbers = [n for n in row['numbers'] if n['start'] == prior['start'] and n['end'] >= prior['end']]
        if len(numbers) != 1 or not numbers[0]['raw'].startswith(prior['raw']):
            raise ValueError('Correction does not extend the same original numeric prefix')
        number = numbers[0]
        selection = {'token_id': number['token_id'], 'metric': old['metric'],
            'source_unit': old['source_unit'], 'unit_ref': row['refs'][0]['ref_id'],
            'period_ref': row['refs'][0]['ref_id'], 'period_text': old['period_claim'],
            'basis': old['basis'], 'role': old['role']}
        bound, _, _ = numeric_binding.bind(bundle, table, [selection], minimum_facts=1)
        bound[0]['fact_id'] = 'V' + str(len(variables) + 1); variables += bound
        corrections.append({'old_raw': prior['raw'], 'new_raw': number['raw'],
            'old_normalized': old['normalized_value'], 'new_normalized': bound[0]['normalized_value'],
            'original_row_ref': ref, 'old_numeric_token': prior, 'new_numeric_token': number})
    formulas.validate_variables(bundle, variables)
    stage = task / 'currency-token-repair-v1'
    save(stage / 'variables.json', variables)
    save(stage / 'audit.json', {'corrections': corrections, 'old_facts_and_outputs_preserved': True,
        'new_generation_calls': 0, 'new_search_calls': 0, 'numeric_implementation_sha256': analysis.sha(facts.__file__)})
    folder = task / 'analysis-currency-repair-v1'; folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8='1')
    for key in ('METACULUS_TOKEN', 'TAVILY_API_KEY', 'TAVILY_API_KEY2', 'EXA_API_KEY', 'EXA_API_KEY2'):
        env.pop(key, None)
    with (folder / 'stdout.log').open('a', encoding='utf-8') as out, (folder / 'stderr.log').open('a', encoding='utf-8') as err:
        subprocess.run([sys.executable, '-X', 'utf8', '-u', '-m', 'ForecastAgent.market_pulse.batch_analysis',
            '--package', str(source), '--root', str(folder), '--reuse-facts', str(stage / 'variables.json')],
            env=env, stdout=out, stderr=err)
    result = load(folder / 'result.json')
    print(json.dumps({k: result.get(k) for k in ('id', 'status', 'quantiles',
        'manual_delivery_candidate', 'error')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inventory', 'collect', 'analyze', 'supplement', 'submit', 'recover',
        'rescore-units', 'salvage', 'rescue', 'analyze-supplement', 'report-index',
        'target-rows', 'analyze-report-index', 'rescue-pdf', 'report-recovery', 'currency-repair'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ids', help='Optional bounded comma-separated pending cohort')
    args = parser.parse_args()
    if args.command == 'inventory':
        capture(args.root)
    elif args.command == 'collect':
        collect_new_issuers(args.root)
    elif args.command == 'analyze':
        analyze_pending(args.root, args.ids.split(',') if args.ids else None)
    elif args.command == 'supplement':
        if not args.ids or ',' in args.ids:
            raise ValueError('Select one supplement owner question')
        supplement(args.root, args.ids)
    elif args.command == 'submit':
        submit_ready(args.root)
    elif args.command == 'recover':
        recover_pending(args.root, args.ids.split(','))
    elif args.command == 'rescore-units':
        rescore_units(args.root, args.ids)
    elif args.command == 'salvage':
        salvage_saved_facts(args.root, args.ids)
    elif args.command == 'rescue':
        rescue_supplement(args.root, args.ids)
    elif args.command == 'analyze-supplement':
        analyze_supplement(args.root, args.ids.split(','))
    elif args.command == 'report-index':
        original_report_index(args.root, args.ids)
    elif args.command == 'rescue-pdf':
        rescue_report_pdfs(args.root, args.ids)
    elif args.command == 'report-recovery':
        recover_saved_report_facts(args.root, args.ids.split(','))
    elif args.command == 'currency-repair':
        repair_saved_currency_tokens(args.root, args.ids)
    else:
        analyze_target_rows(args.root, args.ids.split(','), shared_index=args.command == 'analyze-report-index')
