"""Bounded discovery, durable waiting pool, quote snapshots and delayed labels."""
from collections import Counter
import copy
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import PublicHTTP, digest, instant, now, policy, read, seal, state, write
from .tracking import creation_schedule, forecast_schedule, labels, targets_for, final_record
from .venues import GAMMA, KALSHI, CRYPTO, GAMBLING, book, normalize, refresh, screen, final_resolution
from .discovery import scan as scan_creation_window


def discover(http, venue, cfg, clock):
    rows, pages, cursor = [], [], None
    start = int((instant(clock) - timedelta(hours=cfg['maximum_candidate_age_hours'])).timestamp())
    for index in range(cfg['maximum_discovery_pages_per_venue']):
        if venue == 'polymarket':
            raw, receipt = http.get(GAMMA + 'markets', {'closed': 'false', 'order': 'createdAt',
                                   'ascending': 'false', 'limit': 100, 'offset': index * 100})
            chunk, next_cursor = raw, None
        else:
            params = {'mve_filter': 'exclude', 'limit': 100, 'min_created_ts': start}
            if cursor:
                params['cursor'] = cursor
            raw, receipt = http.get(KALSHI + 'markets', params)
            chunk, next_cursor = raw['markets'], raw.get('cursor')
        if not isinstance(chunk, list):
            raise ValueError('Unexpected market page shape')
        pages.append(receipt)
        rows.extend(chunk)
        if len(chunk) < 100 and venue == 'polymarket' or venue == 'kalshi' and not next_cursor:
            return rows, {'complete': True, 'pages': pages, 'window_start': start}
        if venue == 'polymarket' and chunk and all(r.get('createdAt') and instant(r['createdAt']).timestamp() < start for r in chunk):
            return rows, {'complete': True, 'pages': pages, 'window_start': start}
        cursor = next_cursor
    # Bounded discovery may miss candidates. A partial scan is never a completeness claim.
    return rows, {'complete': False, 'reason': 'page_cap', 'pages': pages, 'window_start': start}


def discover_events(http, venue, cfg, clock):
    """Event-level filtering avoids saturation by short crypto/sports contracts."""
    rows, pages, cursor = [], [], None
    close_min = instant(clock) + timedelta(hours=cfg['minimum_close_buffer_hours'])
    excluded_tags = []
    if venue == 'polymarket':
        for slug in ('sports', 'crypto'):
            tag, receipt = http.get(GAMMA + 'tags/slug/' + slug)
            if str(tag.get('slug')) != slug or not str(tag.get('id', '')).isdigit():
                raise ValueError('Exclusion tag identity mismatch')
            excluded_tags.append(tag['id']); pages.append(receipt)
    for index in range(cfg['maximum_discovery_pages_per_venue']):
        if venue == 'polymarket':
            page_size = 20
            response, receipt = http.get(GAMMA + 'events', {'closed': 'false', 'active': 'true',
                'order': 'createdAt', 'ascending': 'false', 'limit': page_size, 'offset': index * page_size,
                'end_date_min': close_min.isoformat(), 'exclude_tag_id': excluded_tags})
            events, next_cursor = response, None
        else:
            params = {'status': 'open', 'with_nested_markets': 'true', 'limit': 100,
                      'min_close_ts': int(close_min.timestamp())}
            if cursor: params['cursor'] = cursor
            response, receipt = http.get(KALSHI + 'events', params)
            events, next_cursor = response['events'], response.get('cursor')
        pages.append(receipt)
        for event in events:
            text = ' '.join(str(event.get(k) or '') for k in ('title', 'category', 'tags', 'ticker', 'event_ticker'))
            if CRYPTO.search(text) or GAMBLING.search(text):
                continue
            for original in event.get('markets') or []:
                market = original.copy()
                if venue == 'polymarket':
                    # Preserve provenance without recursively copying all sibling markets.
                    market['events'] = [{k: v for k, v in event.items() if k in
                        {'id', 'title', 'slug', 'description', 'resolutionSource', 'tags', 'category'}}]
                else:
                    market['category'] = event.get('category')
                rows.append(market)
        complete = venue == 'polymarket' and len(events) < page_size or venue == 'kalshi' and not next_cursor
        if complete:
            return rows, {'complete': True, 'pages': pages, 'scope': 'eligible_close_window_events', 'close_min': close_min.isoformat()}
        cursor = next_cursor
    return rows, {'complete': False, 'reason': 'page_cap', 'pages': pages,
                  'scope': 'eligible_close_window_events', 'close_min': close_min.isoformat()}


def key_not_enrolled(ledger, market):
    return market['key'] not in ledger['tasks']


def ingest(ledger, venue, rows, cfg, clock):
    for raw in rows:
        market = normalize(venue, raw)
        if market.get('created_at') and key_not_enrolled(ledger, market) and instant(market['created_at']) < instant(clock) - timedelta(hours=cfg['maximum_candidate_age_hours']):
            continue
        key = market['key']
        previous = ledger['markets'].get(key, {})
        version = digest(raw)
        history = list(previous.get('metadata_versions', []))
        if not history or history[-1]['sha256'] != version:
            history.append({'sha256': version, 'seen_at': clock, 'rules_sha256': market['rules_sha256']})
        if key in ledger['tasks']:
            market.pop('terminal_result', None)
            market.pop('final_observation', None)
        ledger['markets'][key] = {**previous, **market, 'first_seen_at': previous.get('first_seen_at', clock),
                                 'last_seen_at': clock, 'metadata_versions': history,
                                 'selection': screen(market, clock, cfg)}


def admission_window(ledger, cfg, clock):
    """Count immutable admissions, including blocked/failed tasks, across midnight."""
    current = instant(clock)
    hours = cfg['enrollment_window_hours']
    start = current - timedelta(hours=hours)
    stamps = sorted(instant(task['enrolled_at']) for task in ledger['tasks'].values())
    if any(stamp > current for stamp in stamps):
        raise ValueError('Future enrollment receipt; refusing to admit beyond a trustworthy window')
    recent = [stamp for stamp in stamps if start < stamp <= current]
    cap = cfg['maximum_new_tasks_per_day']
    return {'mode': 'rolling_24_hours', 'window_hours': hours, 'start_exclusive': start.isoformat(),
            'end_inclusive': current.isoformat(), 'cap': cap, 'admitted': len(recent),
            'remaining': max(0, cap-len(recent)), 'cap_reached': len(recent) >= cap,
            'next_slot_at': (recent[len(recent)-cap] + timedelta(hours=hours)).isoformat()
                if len(recent) >= cap else None,
            'all_statuses_count': True, 'budget_reset': False}


def enroll(ledger, cfg, clock):
    zone = ZoneInfo(cfg['enrollment_day_timezone'])
    date = instant(clock).astimezone(zone).date().isoformat()
    # Calendar-day counts are reporting only; no midnight allowance reset.
    count = admission_window(ledger, cfg, clock)['admitted']
    families = {t['market']['family'] for t in ledger['tasks'].values()}
    ordered = sorted(ledger['markets'].values(), key=lambda m: m.get('created_at') or m['first_seen_at'], reverse=True)
    for market in ordered:
        if count >= cfg['maximum_new_tasks_per_day']:
            break
        market['selection'] = screen(market, clock, cfg)
        if market['selection']['status'] != 'candidate' or (market.get('last_quote') or {}).get('status') != 'usable':
            continue
        observed = market['last_quote'].get('received_at')
        if not observed or not instant(ledger.get('last_attempt_at', clock)) <= instant(observed) <= instant(clock):
            # An old usable book cannot masquerade as the admission-time price.
            continue
        if market['family'] in families:
            continue
        key = market['key']
        # One immutable representative per venue event family. No cross-venue title matching.
        from .deployment import task_release
        ledger['tasks'][key] = {'key': key, 'market': copy.deepcopy(market), 'enrolled_at': clock,
                                'status': 'queued', 'policy_sha256': ledger['identity']['policy_sha256'],
                                'price_schedule': creation_schedule({'market': market}),
                                'acquisition_policy': 'market-evidence-capture-v2', **task_release()}
        families.add(market['family'])
        count += 1
    ledger.setdefault('enrollment_daily', {})[date] = sum(
        instant(task['enrolled_at']).astimezone(zone).date().isoformat() == date for task in ledger['tasks'].values())
    ledger['admission_window'] = admission_window(ledger, cfg, clock)


def quote_turns(ledger, worker, candidates, cfg, clock):
    """Due label windows first, then fair turns for active tasks and new families."""
    current = instant(clock)
    urgent, active = [], []
    for key in ledger['tasks']:
        task = worker.get('tasks', {}).get(key, {})
        targets = targets_for(ledger['tasks'][key], task)
        market = ledger['markets'][key]
        if market.get('terminal_result') or not market.get('tradable', True):
            continue
        if any(t - timedelta(minutes=cfg['label_window_minutes']) <= current <= t for t in targets):
            urgent.append(key)
        elif targets and current <= max(targets) or task.get('status', 'queued') in {'queued', 'running', 'retry_wait'}:
            active.append(key)
    def age(key):
        market = ledger['markets'][key]
        return ((market.get('last_quote') or {}).get('received_at') or market['first_seen_at'], key)
    active.sort(key=age)
    urgent.sort(key=age)
    cap = cfg['maximum_quote_requests_per_dispatch']
    turns = urgent[:cap]
    # Reserve half of remaining turns for new families; neither class starves.
    left = cap - len(turns)
    turns.extend(candidates[:(left + 1) // 2])
    turns.extend(active[:cap - len(turns)])
    for key in candidates + active:
        if len(turns) >= cap:
            break
        if key not in turns:
            turns.append(key)
    return turns


def label(task, market, quotes, clock, cfg):
    return labels(forecast_schedule(task['forecast']), market, quotes, clock, cfg) if task.get('forecast') else {}


def metadata_turns(ledger, worker, quote_keys, cfg, clock):
    # Continue settlement polling after the price experiment's last horizon.
    due = []
    for key in ledger['tasks']:
        market = ledger['markets'][key]
        hours = cfg['resolved_recheck_interval_hours'] if market.get('terminal_result') else cfg['final_refresh_interval_hours']
        previous = market.get('last_followup_check_at')
        if not previous or instant(clock) - instant(previous) >= timedelta(hours=hours):
            due.append(key)
    due.sort(key=lambda k: (ledger['markets'][k].get('last_followup_check_at', ''), k))
    # Reserve half the metadata capacity for due final checks, even with new launches.
    cap = cfg['maximum_metadata_refresh_per_dispatch']
    keys = [key for key in quote_keys if key in ledger['tasks'] and any(
        target - timedelta(minutes=cfg['label_window_minutes']) <= instant(clock) <= target
        for target in targets_for(ledger['tasks'][key], worker.get('tasks', {}).get(key, {})))][:cap]
    final_slots = (cap - len(keys)) // 2
    keys.extend([key for key in due if key not in keys and key not in quote_keys][:final_slots])
    for key in quote_keys + due:
        if key not in keys and len(keys) < cap:
            keys.append(key)
    return keys


def save_followups(root, ledger, worker, cfg, clock):
    ledger.setdefault('followups', {})
    for key, task in ledger['tasks'].items():
        market = ledger['markets'][key]
        path = root / 'quotes' / (digest(key) + '.json')
        quotes = read(path) if path.exists() else []
        schedule_ = creation_schedule(task)
        final = final_record(market, task['market']['rules_sha256'])
        record = {'key': key, 'schedule': schedule_, 'observed_at': clock,
            'prices': labels(schedule_, market, quotes, clock, cfg), 'final': final,
            'last_followup_check_at': market.get('last_followup_check_at'),
            'collection_independent_of_forecast_status': True,
            'legacy_forecast_targets_preserved': True}
        ledger['followups'][key] = record
        write(root / 'followups' / (digest(key) + '.json'), record)
        forecast = worker.get('tasks', {}).get(key, {}).get('forecast')
        if forecast:
            write(root / 'labels' / (digest(key) + '.json'),
                  labels(forecast_schedule(forecast), market, quotes, clock, cfg))
    # A small control record is embedded by the incremental local sync catalog.
    write(root / 'tracking.json', {'schema': 'market-creation-followups-v1',
        'observed_at': clock, 'tasks': ledger['followups']})


def run(root, worker_root=None, http=None, cfg=None, clock=None):
    root = Path(root); cfg = cfg or policy(); clock = clock or now()
    ledger = state(root, 'monitor', cfg)
    ledger.update(last_attempt_at=clock, last_run_id=__import__('os').environ.get('GITHUB_RUN_ID'))
    write(root / 'state.json', ledger)
    http = http or PublicHTTP(root / 'http', 120)
    errors, scans = [], {}
    try:
        for venue in ('polymarket', 'kalshi'):
            seed = {}
            try:
                # The event path is a bounded latency aid, not the coverage authority.
                rows, seed = discover_events(http, venue, cfg, clock)
                ingest(ledger, venue, rows, cfg, clock)
            except Exception as exc:
                seed = {'complete': False, 'error_type': type(exc).__name__}
                errors.append({'venue': venue, 'stage': 'event_seed', 'error': str(exc)})
            try:
                saved = ledger.setdefault('discovery_windows', {}).setdefault(venue, {})
                # Existing five-page event paths plus four cursor pages per venue
                # use at most20of120 public requests, reserving100for the native
                #40metadata+40book+20resolution follow-up allowances.
                window_cfg = cfg | {'maximum_discovery_pages_per_venue': max(1, cfg['maximum_discovery_pages_per_venue'] - 1)}
                scan = scan_creation_window(http, venue, window_cfg, clock, saved,
                    lambda rows, observed: ingest(ledger, venue, rows, cfg, max((clock, observed), key=instant)))
                scan['event_seed'] = seed
                scans[venue] = scan
                errors.extend(dict(error, venue=venue) for error in scan['errors'])
            except Exception as exc:
                scans[venue] = {'complete': False, 'error_type': type(exc).__name__, 'event_seed': seed}
                errors.append({'venue': venue, 'stage': 'discovery', 'error': str(exc)})
        # A question can be published DURING a multi-page scan. Enrollment uses
        # actual observation time, not the earlier scan-start clock; otherwise
        # genuinely new questions appear to have impossible future timestamps.
        observed_times = [clock]
        for scan in scans.values():
            observed_times.extend(p['received_at'] for p in scan.get('pages', []))
            observed_times.extend(p['received_at'] for p in scan.get('new_market_overlap', {}).get('pages', []))
            observed_times.extend(p['received_at'] for p in scan.get('event_seed', {}).get('pages', []))
        clock = max(observed_times, key=instant)
        worker = state(worker_root, 'worker', cfg) if worker_root and (Path(worker_root) / 'state.json').exists() else {'tasks': {}}
        if worker.get('identity') and worker['identity']['policy_sha256'] != ledger['identity']['policy_sha256']:
            raise ValueError('Worker and monitor policy mismatch')
        # Prioritize enrolled contracts; newest launches cannot starve labels.
        selected = {k for k in ledger['tasks']}
        for market in ledger['markets'].values():
            market['selection'] = screen(market, clock, cfg)
        candidates = [k for k, m in ledger['markets'].items() if m['selection']['status'] in {'candidate', 'waiting_market', 'waiting_rules'}]
        # Try one representative per family before any second threshold. A large
        # new event must not consume the entire quote budget and starve others.
        groups = {}
        for key in candidates:
            if key not in selected:
                groups.setdefault(ledger['markets'][key]['family'], []).append(key)
        def rank(key):
            m = ledger['markets'][key]
            return (bool(m.get('last_quote')), -float(m.get('discovery_volume_hint') or 0),
                    -float(m.get('discovery_liquidity_hint') or 0), key)
        groups = {family: sorted(keys, key=rank) for family, keys in groups.items()}
        rounds = [keys[i] for i in range(max((len(g) for g in groups.values()), default=0))
                  for family, keys in sorted(groups.items()) if i < len(keys)]
        priority = quote_turns(ledger, worker, rounds, cfg, clock)
        refresh_keys = metadata_turns(ledger, worker, priority, cfg, clock)
        resolution_attempts = 0
        refresh_failed = set()
        for key in refresh_keys:
            market = ledger['markets'][key]
            try:
                current, receipt = refresh(http, market)
                clock = max((clock, receipt['received_at']), key=instant)
                if key in selected and current.get('created_at') != ledger['tasks'][key]['market']['created_at']:
                    market['creation_time_drift'] = {'seen_at': receipt['received_at'],
                        'original': ledger['tasks'][key]['market']['created_at'], 'refreshed': current.get('created_at')}
                if market['venue'] == 'polymarket':
                    # A metadata response has no explicit winner; only a successful
                    # resolution response may replace the previous terminal state.
                    current.pop('terminal_result', None)
                market.update(current, metadata_refreshed_at=receipt['received_at'])
                market['selection'] = screen(market, receipt['received_at'], cfg)
                market['metadata_versions'].append({'sha256': receipt['response_sha256'],
                    'seen_at': receipt['received_at'], 'rules_sha256': current['rules_sha256'],
                    'source': 'detail_refresh'})
                if key in selected and current['rules_sha256'] != ledger['tasks'][key]['market']['rules_sha256']:
                    market['rule_drift'] = True
                    market.setdefault('rule_drift_seen_at', receipt['received_at'])
                final = market.get('final_observation', {})
                if (key in selected and market['venue'] == 'polymarket' and not market['tradable']):
                    if resolution_attempts < cfg['maximum_resolution_requests_per_dispatch']:
                        resolution_attempts += 1
                        final = final_resolution(http, market)
                        if final.get('observed_at'):
                            clock = max((clock, final['observed_at']), key=instant)
                    else:
                        final = {'status': 'resolution_check_deferred', 'result': None,
                                 'observed_at': receipt['received_at']}
                if final:
                    market['final_observation'] = final
                    market['terminal_result'] = final.get('result') if final.get('status') == 'resolved' else None
                    if market['terminal_result']:
                        market.setdefault('terminal_seen_at', final['observed_at'])
                    history = market.setdefault('final_versions', [])
                    value_hash = digest({k: final.get(k) for k in ('status', 'result', 'resolved_at')})
                    if not history or history[-1]['value_sha256'] != value_hash:
                        history.append({'value_sha256': value_hash, 'observation': copy.deepcopy(final)})
                market['last_followup_check_at'] = receipt['received_at']
            except Exception as exc:
                refresh_failed.add(key)
                market['last_followup_attempt_at'] = clock
                errors.append({'key': key, 'stage': 'metadata_or_resolution_refresh', 'error': str(exc)})
        for key in priority:
            market = ledger['markets'][key]
            if key not in refresh_keys:
                errors.append({'key': key, 'stage': 'quote_deferred', 'error': 'metadata_capacity_reserved_for_final_followups'})
                continue
            try:
                q = book(http, market, cfg)
            except Exception as exc:
                q = {'status': 'fetch_failed', 'received_at': now(), 'error': str(exc), 'midpoint': None}
            clock = max((clock, q['received_at']), key=instant)
            q['rules_sha256'] = market['rules_sha256']
            if key in refresh_failed or key not in refresh_keys:
                q.update(status='metadata_unverified', midpoint=None)
            if not market['tradable'] and q.get('status') == 'usable':
                q.update(status='not_orderable', midpoint=None)
            if market.get('rule_drift'):
                q.update(status='rules_changed', midpoint=None)
            market['last_quote'] = q
            if q['status'] == 'usable':
                market.setdefault('first_tradable_seen_at', q['received_at'])
            quotes_path = root / 'quotes' / (digest(key) + '.json')
            quotes = read(quotes_path) if quotes_path.exists() else []
            quotes.append(q); write(quotes_path, quotes)
        for market in ledger['markets'].values():
            market['selection'] = screen(market, clock, cfg)
        enroll(ledger, cfg, clock)
        save_followups(root, ledger, worker, cfg, clock)
        ledger['enrollment_cutoff_utc'] = clock
        ledger['last_scan'] = scans
        ledger['errors'] = errors
        if all(x['complete'] for x in scans.values()) and not errors:
            ledger['last_complete_success_at'] = clock
        ledger['heartbeat'] = {'discovery_complete': all(x['complete'] for x in scans.values()),
                               'discovery_coverage': {venue: {k: v for k, v in scan.items() if k not in ('pages', 'event_seed')}
                                                      for venue, scan in scans.items()},
                               'waiting_or_excluded_distribution': dict(Counter(m['selection']['status'] for m in ledger['markets'].values())),
                               'queued_families': len(ledger['tasks']), 'no_model_calls': True,
                               'candidate_age_limit_hours': cfg['maximum_candidate_age_hours'],
                               'minimum_resolution_hours': cfg['minimum_resolution_hours'],
                               'maximum_new_families_per_day': cfg['maximum_new_tasks_per_day'],
                               'enrollment_day_timezone': cfg['enrollment_day_timezone'],
                               'enrollment_daily': ledger['enrollment_daily'],
                               'admission_window': ledger['admission_window'],
                               'price_target_origin': 'market_created_at',
                               'price_horizons_hours': [24, 72, 168],
                               'final_status_distribution': dict(Counter(t['final']['status'] for t in ledger['followups'].values())),
                               'errors_count': len(errors),
                               'scan_shortfall_visible': True, 'public_http_attempts': http.calls}
        write(root / 'report.json', ledger['heartbeat'])
        return ledger['heartbeat']
    finally:
        write(root / 'state.json', ledger)
        seal(root)
