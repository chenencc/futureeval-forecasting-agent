"""Bounded discovery, durable waiting pool, quote snapshots and delayed labels."""
from collections import Counter
import copy
from datetime import timedelta
from pathlib import Path

from .common import PublicHTTP, digest, instant, now, policy, read, seal, state, write
from .venues import GAMMA, KALSHI, CRYPTO, GAMBLING, book, normalize, refresh, screen


def discover(http, venue, cfg, clock):
    rows, pages, cursor = [], [], None
    start = int((instant(clock) - timedelta(days=cfg['maximum_candidate_age_days'])).timestamp())
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


def ingest(ledger, venue, rows, cfg, clock):
    for raw in rows:
        market = normalize(venue, raw)
        if market.get('created_at') and instant(market['created_at']) < instant(clock) - timedelta(days=cfg['maximum_candidate_age_days']):
            continue
        key = market['key']
        previous = ledger['markets'].get(key, {})
        version = digest(raw)
        history = list(previous.get('metadata_versions', []))
        if not history or history[-1]['sha256'] != version:
            history.append({'sha256': version, 'seen_at': clock, 'rules_sha256': market['rules_sha256']})
        ledger['markets'][key] = {**previous, **market, 'first_seen_at': previous.get('first_seen_at', clock),
                                 'last_seen_at': clock, 'metadata_versions': history,
                                 'selection': screen(market, clock, cfg)}


def enroll(ledger, cfg, clock):
    date = instant(clock).date().isoformat()
    count = ledger['daily'].setdefault(date, 0)
    families = {t['market']['family'] for t in ledger['tasks'].values()}
    ordered = sorted(ledger['markets'].values(), key=lambda m: m.get('created_at') or m['first_seen_at'], reverse=True)
    for market in ordered:
        if count >= cfg['maximum_new_tasks_per_utc_day'] or len(families) >= cfg['maximum_active_families']:
            break
        if market['selection']['status'] != 'candidate' or (market.get('last_quote') or {}).get('status') != 'usable':
            continue
        if market['family'] in families:
            continue
        key = market['key']
        # One immutable representative per venue event family. No cross-venue title matching.
        ledger['tasks'][key] = {'key': key, 'market': copy.deepcopy(market), 'enrolled_at': clock,
                                'status': 'queued', 'policy_sha256': ledger['identity']['policy_sha256']}
        families.add(market['family'])
        count += 1
    ledger['daily'][date] = count


def label(task, market, quotes, clock, cfg):
    frozen = task.get('forecast')
    if not frozen:
        return {}
    result = {}
    for hours in cfg['horizons_hours']:
        target = instant(frozen['anchor_at']) + timedelta(hours=hours)
        if instant(clock) < target:
            continue
        window_start = target - timedelta(minutes=cfg['label_window_minutes'])
        if market.get('rule_drift_seen_at') and instant(market['rule_drift_seen_at']) <= target:
            result[str(hours)] = {'status': 'rules_changed', 'target_at': target.isoformat(),
                                  'excluded_from_repricing_mae': True}
            continue
        usable = [q for q in quotes if q.get('status') == 'usable' and
                  window_start <= instant(q['received_at']) <= target]
        if usable:
            chosen = max(usable, key=lambda q: q['received_at'])
            result[str(hours)] = {'status': 'quoted', 'target_at': target.isoformat(), 'quote': chosen,
                                  'label': chosen['midpoint'], 'window_minutes': cfg['label_window_minutes']}
        elif market.get('terminal_result') and market.get('terminal_seen_at') and instant(market['terminal_seen_at']) <= target:
            result[str(hours)] = {'status': 'early_resolution', 'target_at': target.isoformat(),
                                  'terminal_value': 1 if market['terminal_result'] == 'yes' else 0,
                                  'excluded_from_repricing_mae': True}
        else:
            result[str(hours)] = {'status': 'missing_window_quote', 'target_at': target.isoformat(),
                                  'excluded_from_repricing_mae': True, 'no_late_quote_substitution': True}
    return result


def run(root, worker_root=None, http=None, cfg=None, clock=None):
    root = Path(root); cfg = cfg or policy(); clock = clock or now()
    ledger = state(root, 'monitor', cfg)
    ledger.update(last_attempt_at=clock, last_run_id=__import__('os').environ.get('GITHUB_RUN_ID'))
    write(root / 'state.json', ledger)
    http = http or PublicHTTP(root / 'http', 120)
    errors, scans = [], {}
    try:
        for venue in ('polymarket', 'kalshi'):
            try:
                rows, scan = discover_events(http, venue, cfg, clock)
                # A small newest-market overlap also observes new children under old events.
                overlap_cfg = cfg | {'maximum_discovery_pages_per_venue': 1}
                overlap, overlap_audit = discover(http, venue, overlap_cfg, clock)
                scan['new_market_overlap'] = overlap_audit
                rows.extend(overlap)
                ingest(ledger, venue, rows, cfg, clock)
                scans[venue] = scan
            except Exception as exc:
                scans[venue] = {'complete': False, 'error_type': type(exc).__name__}
                errors.append({'venue': venue, 'stage': 'discovery', 'error': str(exc)})
        # A question can be published DURING a multi-page scan. Enrollment uses
        # actual observation time, not the earlier scan-start clock; otherwise
        # genuinely new questions appear to have impossible future timestamps.
        observed_times = [clock]
        for scan in scans.values():
            observed_times.extend(p['received_at'] for p in scan.get('pages', []))
            observed_times.extend(p['received_at'] for p in scan.get('new_market_overlap', {}).get('pages', []))
        clock = max(observed_times, key=instant)
        worker = read(Path(worker_root) / 'state.json') if worker_root and (Path(worker_root) / 'state.json').exists() else {'tasks': {}}
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
        priority = list(sorted(selected)) + rounds
        for index, key in enumerate(priority[:cfg['maximum_quote_requests_per_dispatch']]):
            market = ledger['markets'][key]
            if index < cfg['maximum_metadata_refresh_per_dispatch']:
                try:
                    current, receipt = refresh(http, market)
                    market.update(current, metadata_refreshed_at=receipt['received_at'])
                    market['selection'] = screen(market, receipt['received_at'], cfg)
                    market['metadata_versions'].append({'sha256': receipt['response_sha256'],
                        'seen_at': receipt['received_at'], 'rules_sha256': current['rules_sha256'],
                        'source': 'detail_refresh'})
                    if current.get('terminal_result'):
                        market.setdefault('terminal_seen_at', receipt['received_at'])
                    if key in selected and current['rules_sha256'] != ledger['tasks'][key]['market']['rules_sha256']:
                        market['rule_drift'] = True
                        market.setdefault('rule_drift_seen_at', receipt['received_at'])
                except Exception as exc:
                    errors.append({'key': key, 'stage': 'metadata_refresh', 'error': str(exc)})
            try:
                q = book(http, market, cfg)
            except Exception as exc:
                q = {'status': 'fetch_failed', 'received_at': now(), 'error': str(exc), 'midpoint': None}
            clock = max((clock, q['received_at']), key=instant)
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
            if key in worker['tasks'] and worker['tasks'][key].get('forecast'):
                labels = label(worker['tasks'][key], market, quotes, clock, cfg)
                write(root / 'labels' / (digest(key) + '.json'), labels)
        for market in ledger['markets'].values():
            market['selection'] = screen(market, clock, cfg)
        enroll(ledger, cfg, clock)
        ledger['enrollment_cutoff_utc'] = clock
        ledger['last_scan'] = scans
        ledger['errors'] = errors
        if all(x['complete'] for x in scans.values()) and not errors:
            ledger['last_complete_success_at'] = clock
        ledger['heartbeat'] = {'discovery_complete': all(x['complete'] for x in scans.values()),
                               'waiting_or_excluded_distribution': dict(Counter(m['selection']['status'] for m in ledger['markets'].values())),
                               'queued_families': len(ledger['tasks']), 'no_model_calls': True,
                               'candidate_age_limit_hours': cfg['maximum_candidate_age_days'] * 24,
                               'minimum_resolution_hours': cfg['minimum_resolution_hours'],
                               'pilot_family_cap': cfg['maximum_active_families'],
                               'errors_count': len(errors),
                               'scan_shortfall_visible': True, 'public_http_attempts': http.calls}
        write(root / 'report.json', ledger['heartbeat'])
        return ledger['heartbeat']
    finally:
        write(root / 'state.json', ledger)
        seal(root)
