"""Resumable contract discovery. Coverage refers to a declared API window only."""
import copy
from datetime import timedelta

from .common import digest, instant
from .venues import GAMMA, KALSHI

VERSION = 'creation-window-discovery-v1'
OVERLAP_MINUTES = 20


def new_window(venue, cfg, clock, previous=None):
    end = instant(clock)
    lower = end - timedelta(hours=cfg['maximum_candidate_age_hours'])
    if previous:
        lower = max(lower, instant(previous) - timedelta(minutes=OVERLAP_MINUTES))
    return {'start': lower.isoformat(), 'end': end.isoformat(), 'cursor': None,
            'pages': 0, 'seen_ids': [], 'cursor_hashes': [], 'last_created_at': None,
            'close_min': (end + timedelta(hours=cfg['minimum_close_buffer_hours'])).isoformat(),
            'venue': venue}


def page(http, venue, window):
    """Use documented cursor endpoints; never interpret an offset as a cursor."""
    if venue == 'polymarket':
        params = {'closed': 'false', 'order': 'createdAt', 'ascending': 'false',
                  'limit': 100, 'include_tag': 'true', 'end_date_min': window['close_min']}
        if window['cursor']:
            params['after_cursor'] = window['cursor']
        response, receipt = http.get(GAMMA + 'markets/keyset', params)
        cursor_field = 'next_cursor'
    elif venue == 'kalshi':
        params = {'mve_filter': 'exclude', 'limit': 1000,
                  # Include both boundary seconds; local filtering uses exact clocks.
                  'min_created_ts': int(instant(window['start']).timestamp()) - 1,
                  'max_created_ts': int(instant(window['end']).timestamp()) + 1}
        if window['cursor']:
            params['cursor'] = window['cursor']
        response, receipt = http.get(KALSHI + 'markets', params)
        cursor_field = 'cursor'
    else:
        raise ValueError('Unsupported discovery venue')
    if not isinstance(response, dict) or not isinstance(response.get('markets'), list):
        raise ValueError('Unexpected contract cursor page shape')
    rows, cursor = response['markets'], response.get(cursor_field)
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError('Invalid cursor type')
    if cursor and (cursor == window['cursor'] or digest(cursor) in window['cursor_hashes']):
        raise ValueError('Repeated discovery cursor')
    if not rows and cursor:
        raise ValueError('Empty discovery page has a continuation cursor')
    clocks, ids = [], []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid contract row')
        ident = row.get('id' if venue == 'polymarket' else 'ticker')
        stamp = row.get('createdAt' if venue == 'polymarket' else 'created_time')
        if ident is None or not str(ident).strip() or not isinstance(stamp, str):
            raise ValueError('Missing contract identity or creation clock')
        parsed = instant(stamp)
        if parsed.utcoffset() is None:
            raise ValueError('Creation clock must include timezone')
        ids.append(str(ident)); clocks.append(parsed)
    # Only Polymarket documents requested creation ordering. Kalshi uses server bounds.
    if venue == 'polymarket':
        prior = instant(window['last_created_at']) if window['last_created_at'] else None
        if any(a < b for a, b in zip(clocks, clocks[1:])) or (prior and clocks and clocks[0] > prior):
            raise ValueError('Creation ordering changed inside a frozen cursor scan')
    seen = set(window['seen_ids'])
    selected = []
    lower, upper = instant(window['start']), instant(window['end'])
    for row, ident, stamp in zip(rows, ids, clocks):
        if lower <= stamp <= upper and ident not in seen:
            selected.append(row); seen.add(ident)
    boundary = venue == 'polymarket' and bool(clocks) and clocks[-1] < lower
    done = not cursor or boundary
    next_window = copy.deepcopy(window)
    next_window.update(cursor=cursor, pages=window['pages'] + 1,
                       seen_ids=sorted(seen), last_created_at=clocks[-1].isoformat() if clocks else window['last_created_at'])
    if cursor:
        next_window['cursor_hashes'].append(digest(cursor))
    return selected, next_window, done, receipt, len(rows)


def scan(http, venue, cfg, clock, saved, consume):
    """Commit rows and cursor in the SAME native ledger; interruption safely replays.

    A resumed scan also gets one fresh head page. The head is a latency aid and
    cannot advance the coverage watermark. HTTP failures never advance a cursor.
    """
    if saved and saved.get('version') != VERSION:
        raise ValueError('Unknown saved discovery protocol')
    saved.setdefault('version', VERSION)
    saved.setdefault('windows', [])
    saved.setdefault('gaps', [])
    pages, errors = [], []
    limit = cfg['maximum_discovery_pages_per_venue']
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError('Discovery page budget must be positive')
    lower = instant(clock) - timedelta(hours=cfg['maximum_candidate_age_hours'])
    active = saved.get('active')
    if active and instant(active['end']) < lower:
        saved['gaps'].append({'reason': 'unfinished_window_expired', 'observed_at': clock,
                              'start': active['start'], 'end': active['end'],
                              'cursor_sha256': digest(active['cursor']), 'pages': active['pages']})
        saved['active'] = active = None
    previous = saved.get('covered_through')
    if previous and instant(previous) > instant(clock):
        raise ValueError('Discovery clock regressed before coverage watermark')
    if not active:
        if previous and instant(previous) < lower:
            gap = {'reason': 'monitor_outage_outside_admission_window',
                   'start': previous, 'end': lower.isoformat(), 'observed_at': clock}
            if not saved['gaps'] or saved['gaps'][-1] != gap:
                saved['gaps'].append(gap)
        saved['active'] = active = new_window(venue, cfg, clock, previous)
    if instant(active['end']) > instant(clock):
        raise ValueError('Discovery clock regressed before frozen window end')

    def receive(window, kind):
        rows, following, done, receipt, count = page(http, venue, window)
        # Consume before acknowledgement; a failed row never loses its cursor.
        consume(rows, receipt['received_at'])
        pages.append(dict(receipt, role=kind, rows=count, ingested_rows=len(rows)))
        return following, done

    # Latest launches must not wait behind the older continuation. Keep at least
    # one request for progress even when the configured budget is one page.
    if active['cursor'] and limit > 1 and instant(active['end']) < instant(clock):
        limit -= 1
        try:
            receive(new_window(venue, cfg, clock), 'fresh_head_not_coverage')
        except Exception as exc:
            errors.append({'stage': 'fresh_head', 'error_type': type(exc).__name__, 'error': str(exc)})
    complete = False
    completed_this_dispatch = []
    for _ in range(limit):
        try:
            following, complete = receive(active, 'window_continuation')
        except Exception as exc:
            errors.append({'stage': 'window_page', 'error_type': type(exc).__name__, 'error': str(exc)})
            break
        saved['active'] = active = following
        if complete:
            record = {k: active[k] for k in ('start', 'end', 'pages')}
            record.update(completed_at=pages[-1]['received_at'], unique_rows=len(active['seen_ids']),
                          reason='creation_boundary_or_cursor_exhausted')
            saved['windows'].append(record)
            completed_this_dispatch.append(record)
            saved['covered_through'] = active['end']
            saved['active'] = None
            if instant(active['end']) >= instant(clock):
                break
            # Finishing an older window does not certify launches since its end.
            saved['active'] = active = new_window(venue, cfg, clock, active['end'])
            complete = False
    # Keep bounded diagnostic history; counts preserve the existence of older gaps.
    saved['gap_count'] = saved.get('gap_count', 0) + max(0, len(saved['gaps']) - 24)
    saved['gaps'] = saved['gaps'][-24:]
    saved['windows'] = saved['windows'][-24:]
    saved['last_attempt_at'] = clock
    audit = {'version': VERSION, 'complete': complete and not errors,
             'reason': 'window_complete' if complete and not errors else 'error' if errors else 'page_budget_yield',
             'scope': 'polymarket_open_contracts_with_minimum_close_buffer' if venue == 'polymarket' else 'kalshi_non_combo_contracts',
             'window_start': active['start'], 'window_end': active['end'],
             'covered_through': saved.get('covered_through'),
             'coverage_lag_seconds': max(0, (instant(clock) - instant(saved['covered_through'])).total_seconds()) if saved.get('covered_through') else None,
             'pending_window': bool(saved.get('active')),
             'pending_cursor': bool(saved.get('active') and saved['active']['cursor']), 'pages': pages, 'errors': errors,
             'historical_gap_count': saved['gap_count'] + len(saved['gaps']),
             'completed_windows_this_dispatch': completed_this_dispatch,
             'platform_wide_completeness_claimed': False}
    saved['last_audit'] = {k: v for k, v in audit.items() if k != 'pages'}
    return audit
