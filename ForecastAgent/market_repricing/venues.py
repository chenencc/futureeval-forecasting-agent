"""Explicit venue schemas; displayed prices never substitute for order books."""
import json
import re
from datetime import timedelta, datetime, timezone

from .common import digest, instant, number

GAMMA = 'https://gamma-api.polymarket.com/'
CLOB = 'https://clob.polymarket.com/'
KALSHI = 'https://external-api.kalshi.com/trade-api/v2/'
CRYPTO = re.compile(r'\b(crypto\w*|bitcoin|btc|ethereum|eth|solana|dogecoin|stablecoin\w*|blockchain|coinbase|binance|microstrategy|digital assets?|token price|nft\w*)\b', re.I)
GAMBLING = re.compile(r'\b(sports?|esports?|soccer|football|basketball|baseball|tennis|hockey|cricket|nba|nfl|nhl|mlb|fifa|ufc|champions league|premier league|rc lens|ol lyonnes|win the game|exact score|touchdowns?|passing yards?|counter.strike|cs2|league of legends|dota|valorant|mentions?|tweets?|followers?|likes?|retweets?|halftime|both teams to score)\b|\bvs\.?\s|\bKX(?:NFL|NBA|MLB|NHL|SOCCER|ESPORTS|CS2|LOL)', re.I)
PRICE_ONLY = re.compile(r'\b(price|trading|trade|close|closing|above|below|hit|reach)\b.{0,65}\b(\$|usd|stock|share price|gold|silver|oil|nasdaq|s&p|dow|dollar)', re.I)
DOMAINS = {
    'macro_policy': r'\b(fed|fomc|interest rate|inflation|cpi|gdp|unemployment|payroll|central bank|ecb|monetary|tariff)\b',
    'company_event': r'\b(earnings|revenue|eps|merger|acquisition|antitrust|product launch|fda approval|deliveries|foot traffic|credit card spend|same.store sales|retail sales)\b',
    'government_law': r'\b(court|lawsuit|ruling|bill|legislation|congress|senate|parliament|ceasefire|treaty|sanction|vote|hearing|government|budget)\b',
    'election': r'\b(election|president|governor|polling|nominee|referendum|debate|midterms)\b',
    'science_health': r'\b(nasa|spacex|launch|spacecraft|clinical trial|outbreak|vaccine|public health|research|ai model|artificial intelligence)\b'
}


def array(value):
    return json.loads(value) if isinstance(value, str) else value


def resolution_hint(venue, raw, rule):
    """Preserve expected settlement vs rule deadline; never alias order close."""
    if venue == 'kalshi':
        for field in ('expected_expiration_time', 'expiration_time'):
            if raw.get(field):
                return {'at': raw[field], 'role': 'platform_expected_expiration', 'source_field': field,
                        'can_resolve_early': bool(raw.get('can_close_early')),
                        'guaranteed_minimum_lifetime': False}
        return {'at': None, 'role': 'unknown', 'can_resolve_early': bool(raw.get('can_close_early'))}
    months = 'January February March April May June July August September October November December'.split()
    pattern = r'\b(' + '|'.join(months) + r')\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b'
    close = instant(raw['endDate']) if raw.get('endDate') else None
    matches = []
    for match in re.finditer(pattern, rule, re.I):
        try:
            stamp = datetime(int(match[3]), next(i+1 for i, m in enumerate(months) if m.lower() == match[1].lower()),
                             int(match[2]), tzinfo=timezone.utc)
        except ValueError:
            continue
        if close and abs((close - stamp).total_seconds()) <= 36 * 3600:
            matches.append((stamp, match.group()))
    if not matches:
        # Bind a delegated title deadline to explicit rule time and platform year.
        delegated = re.search(r'\b(?:specified date|date (?:specified|stated|shown) in (?:the )?title|date in (?:the )?title)\b', rule, re.I)
        late_et = re.search(r'\b11:59\s*PM\s*ET\b', rule, re.I)
        title_dates = list(re.finditer(r'\bby\s+(' + '|'.join(months) + r')\s+(\d{1,2})(?:st|nd|rd|th)?\b', str(raw.get('question', '')), re.I))
        if close and delegated and late_et and len(title_dates) == 1:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                local = close.astimezone(ZoneInfo('America/New_York'))
                match = title_dates[0]
                month = next(i+1 for i, m in enumerate(months) if m.lower() == match[1].lower())
                if (local.month, local.day, local.hour, local.minute, local.second) == (month, int(match[2]), 23, 59, 0):
                    return {'at': close.isoformat(), 'role': 'bound_title_rule_deadline',
                        'source_field': 'question+description+endDate', 'verbatim_date': match.group(),
                        'verbatim_rule_reference': delegated.group(), 'verbatim_time': late_et.group(),
                        'year_source': 'platform_endDate_matched_in_rule_timezone',
                        'timezone': 'America/New_York', 'title_rule_metadata_agree': True,
                        'can_resolve_early': True, 'guaranteed_minimum_lifetime': False}
            except ZoneInfoNotFoundError:
                pass
        return {'at': None, 'role': 'unknown', 'source_field': 'description', 'can_resolve_early': True}
    stamp, quoted = min(matches)
    return {'at': stamp.isoformat(), 'role': 'rule_observation_deadline_day_precision',
            'source_field': 'description', 'verbatim_date': quoted,
            'day_precision_policy': 'Conservative UTC day start; no invented hour or timezone',
            'can_resolve_early': True, 'guaranteed_minimum_lifetime': False}


def normalize(venue, raw):
    if venue == 'polymarket':
        ident = str(raw['id'])
        outcomes, tokens = array(raw.get('outcomes', [])), array(raw.get('clobTokenIds', []))
        binary = len(outcomes) == 2 and {str(o).lower() for o in outcomes} == {'yes', 'no'} and len(tokens) == 2
        index = next((i for i, o in enumerate(outcomes) if str(o).lower() == 'yes'), None)
        events = raw.get('events') or []
        event = events[0] if events else {}
        rule = raw.get('description') or event.get('description') or ''
        sources = [x for x in (raw.get('resolutionSource'), event.get('resolutionSource')) if x]
        return {'key': venue + ':' + ident, 'venue': venue, 'venue_id': ident,
                'family': venue + ':' + str(event.get('id') or ident), 'question': raw.get('question', ''),
                'url': 'https://polymarket.com/event/' + str(event.get('slug') or raw.get('slug', '')),
                'rules': rule, 'rules_sha256': digest(rule), 'rules_sources': sources,
                'created_at': raw.get('createdAt'), 'condition_id': raw.get('conditionId'),
                'yes_outcome_index': index, 'platform_status': 'closed' if raw.get('closed') else 'open', 'close_at': raw.get('endDate'),
                'close_time_role': 'platform_hint_not_authoritative_event_deadline',
                'resolution_hint': resolution_hint(venue, raw, rule),
                'binary': binary, 'yes_token': str(tokens[index]) if binary else None,
                'composite': raw.get('comboStatus') not in {None, '', 'disabled'} or bool(re.search(r'\band\b', str(raw.get('question', '')), re.I)),
                'tradable': bool(raw.get('active') and not raw.get('closed') and not raw.get('archived') and raw.get('enableOrderBook') and raw.get('acceptingOrders')),
                'disputed': 'disputed' in str(raw.get('umaResolutionStatus', '')).lower() or
                            'disputed' in str(raw.get('umaResolutionStatuses', '')).lower(),
                'terminal_result': None,
                'discovery_liquidity_hint': raw.get('liquidityNum') or raw.get('liquidity') or 0,
                'discovery_volume_hint': raw.get('volumeNum') or raw.get('volume') or 0,
                'screen_text': ' '.join([str(raw.get('question', '')), rule, str(event.get('title', '')), str(raw.get('tags', [])), str(event.get('tags', [])), str(raw.get('sportsMarketType', ''))])}
    if venue != 'kalshi':
        raise ValueError('Unsupported venue')
    ticker = str(raw['ticker'])
    rule = '\n\n'.join(str(raw.get(k) or '') for k in ('rules_primary', 'rules_secondary')).strip()
    return {'key': venue + ':' + ticker, 'venue': venue, 'venue_id': ticker,
            'family': venue + ':' + str(raw.get('event_ticker') or ticker),
            'question': str(raw.get('title') or '') + ' ' + str(raw.get('yes_sub_title') or ''),
            'url': 'https://kalshi.com/markets/' + str(raw.get('event_ticker') or ticker).lower(),
            'rules': rule, 'rules_sha256': digest(rule), 'rules_sources': [],
            'created_at': raw.get('created_time'), 'close_at': raw.get('close_time'),
            'close_time_role': 'platform_order_close_not_guaranteed_resolution_time',
            'resolution_hint': resolution_hint(venue, raw, rule),
            'binary': raw.get('market_type') == 'binary', 'yes_token': None,
            'composite': bool(raw.get('mve_collection_ticker')) or bool(re.search(r'\band\b', str(raw.get('title', '')), re.I)),
            'tradable': raw.get('status') in {'open', 'active'}, 'disputed': False,
            'terminal_result': raw.get('result') if raw.get('status') == 'settled' and raw.get('result') in {'yes', 'no'} else None,
            'platform_status': raw.get('status'),
            'final_observation': {'status': 'resolved' if raw.get('status') == 'settled' and raw.get('result') in {'yes', 'no'} else 'pending_settlement' if raw.get('status') in {'closed', 'determined'} else 'unresolved',
                'result': raw.get('result') if raw.get('status') == 'settled' and raw.get('result') in {'yes', 'no'} else None,
                'resolved_at': raw.get('settlement_ts') if raw.get('status') == 'settled' else None,
                'source': 'kalshi_market_detail', 'platform_status': raw.get('status')},
            'discovery_liquidity_hint': raw.get('open_interest_fp') or 0,
            'discovery_volume_hint': raw.get('volume_fp') or 0,
            'screen_text': ' '.join(str(raw.get(k) or '') for k in ('ticker', 'event_ticker', 'title', 'subtitle', 'yes_sub_title', 'rules_primary', 'rules_secondary', 'category'))}


def screen(market, clock, cfg, *, check_newness=True):
    text = market['screen_text']
    for pattern, reason in ((CRYPTO, 'crypto_excluded'), (GAMBLING, 'gambling_or_mentions_excluded'), (PRICE_ONLY, 'price_only_excluded')):
        if pattern.search(text):
            return {'status': 'excluded', 'reason': reason}
    if not market['binary']:
        return {'status': 'excluded', 'reason': 'binary_contract_required_for_quote_v1'}
    if market.get('composite'):
        return {'status': 'excluded', 'reason': 'composite_contract_outside_single_event_pilot'}
    if not market.get('created_at'):
        return {'status': 'waiting_rules', 'reason': 'creation_time_missing'}
    try:
        age = (instant(clock) - instant(market['created_at'])).total_seconds()
    except ValueError:
        return {'status': 'waiting_rules', 'reason': 'invalid_creation_time'}
    if check_newness and not 0 <= age <= cfg['maximum_candidate_age_hours'] * 3600:
        return {'status': 'outside_new_question_window', 'reason': 'outside_creation_age_limit',
                'publication_field': 'createdAt' if market['venue'] == 'polymarket' else 'created_time',
                'publication_time_role': 'platform_creation_proxy_not_updated_or_first_seen', 'age_seconds': age}
    if not market['rules'].strip():
        return {'status': 'waiting_rules', 'reason': 'exact_rules_missing'}
    if market['disputed']:
        return {'status': 'waiting_market', 'reason': 'disputed_rules'}
    # Check hard time/market constraints before spending any semantic-review slot.
    # A keyword match is a latency shortcut, not the complete event taxonomy.
    result = {'catalyst_status': 'plausible_unconfirmed',
              'catalyst_evidence': [], 'close_time_role': market['close_time_role']}
    try:
        remaining = (instant(market['close_at']) - instant(clock)).total_seconds() / 3600
    except (TypeError, ValueError):
        return {**result, 'status': 'waiting_rules', 'reason': 'close_hint_missing'}
    if remaining < cfg['minimum_close_buffer_hours']:
        return {**result, 'status': 'short_resolution_cohort', 'reason': 'insufficient_repricing_window'}
    if remaining > cfg['maximum_close_horizon_days'] * 24:
        return {**result, 'status': 'waiting_catalyst', 'reason': 'distant_event_outside_initial_pilot_window'}
    hint = market.get('resolution_hint') or {}
    try:
        resolution_hours = (instant(hint['at']) - instant(clock)).total_seconds() / 3600
    except (ValueError, KeyError, TypeError):
        return {**result, 'status': 'waiting_rules', 'reason': 'expected_resolution_time_not_established',
                'close_time_is_not_resolution_time': True}
    if resolution_hours < cfg['minimum_resolution_hours']:
        return {**result, 'status': 'short_resolution_cohort', 'reason': 'expected_resolution_within_seven_days',
                'expected_resolution_remaining_hours': resolution_hours, 'resolution_hint': hint}
    result.update(expected_resolution_remaining_hours=resolution_hours, resolution_hint=hint)
    if market.get('terminal_result') or market.get('platform_status') in {'closed', 'settled', 'determined', 'finalized'}:
        return {**result, 'status': 'waiting_market', 'reason': 'contract_not_open'}
    result['orderability_observed'] = market['tradable']
    result['book_required_for_research'] = False
    domain = next((k for k, expression in DOMAINS.items() if re.search(expression, text, re.I)), None)
    if not domain:
        from .intake import reviewed_domain, bound_review
        domain = reviewed_domain(market)
        reviewed = bound_review(market)
        answer = reviewed.get('answer', {})
        if (not domain and answer.get('choice') == 'excluded' and answer.get('confidence', 0) >= .8
                and answer.get('probabilities', {}).get('excluded', 0) >= .8):
            return {**result, 'status': 'excluded', 'reason': 'semantic_event_suitability_excluded',
                    'intake_review_receipt': reviewed['receipt']}
    if not domain:
        return {**result, 'status': 'waiting_relevance', 'reason': 'event_catalyst_not_identified'}
    result['domain'] = domain
    return {**result, 'status': 'candidate', 'reason': 'event_domain_prescreen_only'}


def quote(venue, raw, cfg, metadata):
    def levels(values):
        result = [(number(x['price']), number(x['size'])) for x in values]
        if any(not 0 <= p <= 1 or s < 0 for p, s in result):
            raise ValueError('Invalid book level')
        return result
    if venue == 'polymarket':
        bids, asks = levels(raw.get('bids', [])), levels(raw.get('asks', []))
    else:
        if 'orderbook_fp' not in raw:
            raise ValueError('Kalshi fixed-point dollars schema required; cents are not inferred')
        book = raw['orderbook_fp']
        bids = levels([{'price': p, 'size': s} for p, s in book.get('yes_dollars', [])])
        asks = levels([{'price': 1 - number(p), 'size': s} for p, s in book.get('no_dollars', [])])
    if not bids or not asks:
        return {**metadata, 'status': 'missing_two_sided_book', 'midpoint': None}
    bid = max(p for p, s in bids if s > 0) if any(s > 0 for p, s in bids) else None
    ask = min(p for p, s in asks if s > 0) if any(s > 0 for p, s in asks) else None
    if bid is None or ask is None:
        return {**metadata, 'status': 'missing_depth', 'midpoint': None}
    bid_size = sum(s for p, s in bids if p == bid)
    ask_size = sum(s for p, s in asks if p == ask)
    spread = ask - bid
    quality_flags = ['wide_spread'] if spread > cfg['maximum_spread'] + 1e-9 else []
    status = 'usable'
    if spread < 0:
        status = 'crossed_book'
    elif quality_flags and not cfg.get('allow_wide_spread', False):
        status = 'wide_spread'
    elif min(bid_size, ask_size) < cfg['minimum_top_size']:
        status = 'shallow_book'
    return {**metadata, 'status': status, 'quality_flags': quality_flags,
            'research_only': True, 'midpoint_is_executable_price': False,
            'yes_bid': bid, 'yes_ask': ask,
            'yes_bid_size': bid_size, 'yes_ask_size': ask_size, 'spread': spread,
            'yes_bids_top10': [{'price': p, 'size': sum(s for q, s in bids if q == p)}
                               for p in sorted({p for p, s in bids if s > 0}, reverse=True)[:10]],
            'yes_asks_top10': [{'price': p, 'size': sum(s for q, s in asks if q == p)}
                               for p in sorted({p for p, s in asks if s > 0})[:10]],
            'visible_bid_levels': len({p for p, s in bids if s > 0}),
            'visible_ask_levels': len({p for p, s in asks if s > 0}),
            'midpoint': (bid + ask) / 2 if status == 'usable' else None,
            'price_unit': 'USD_per_1_USD_binary_contract', 'raw_book_sha256': digest(raw)}


def book(http, market, cfg):
    if market['venue'] == 'polymarket':
        raw, receipt = http.get(CLOB + 'book', {'token_id': market['yes_token']})
        if str(raw.get('asset_id', '')) != market['yes_token']:
            raise ValueError('YES asset identity mismatch')
    else:
        raw, receipt = http.get(KALSHI + 'markets/' + market['venue_id'] + '/orderbook', {'depth': 10})
    return quote(market['venue'], raw, cfg, receipt)


def refresh(http, market):
    if market['venue'] == 'polymarket':
        raw, receipt = http.get(GAMMA + 'markets/' + market['venue_id'])
    else:
        response, receipt = http.get(KALSHI + 'markets/' + market['venue_id'])
        raw = response['market']
    value = normalize(market['venue'], raw)
    if value['key'] != market['key']:
        raise ValueError('Refreshed market identity mismatch')
    if market['venue'] == 'polymarket' and not raw.get('events'):
        # The single-market endpoint can omit the parent event. Preserve the
        # family proven by discovery; omission is not a new independent event.
        value['family'] = market['family']
        value['url'] = market['url']
    if market.get('intake_review'):
        from .intake import bound_review
        # Worker freeze checks receive the same bound approval, but rule/entity
        # changes invalidate it before a new price anchor can be accepted.
        review = bound_review(value | {'intake_review': market['intake_review']})
        if review:
            value['intake_review'] = review
    if value.get('final_observation'):
        value['final_observation'].update(observed_at=receipt['received_at'], receipt=receipt)
    return value, receipt


def final_resolution(http, market):
    """Require an explicit winner, never a last price of zero or one."""
    if market['venue'] != 'polymarket':
        return market.get('final_observation') or {'status': 'unresolved', 'result': None}
    if not market.get('condition_id'):
        return {'status': 'resolution_identity_missing', 'result': None}
    raw, receipt = http.get(CLOB + 'markets/' + market['condition_id'])
    if raw.get('condition_id') != market['condition_id']:
        raise ValueError('Resolution condition identity mismatch')
    tokens = raw.get('tokens') or []
    outcomes = {str(t.get('outcome', '')).lower() for t in tokens}
    yes = [t for t in tokens if str(t.get('outcome', '')).lower() == 'yes']
    if (len(tokens) != 2 or outcomes != {'yes', 'no'} or len(yes) != 1 or
            str(yes[0].get('token_id')) != market['yes_token']):
        raise ValueError('Resolution YES asset identity mismatch')
    winners = [t for t in tokens if t.get('winner') is True]
    resolved = (raw.get('closed') is True and len(winners) == 1 and
                all(isinstance(t.get('winner'), bool) for t in tokens) and not market.get('disputed'))
    return {'status': 'resolved' if resolved else 'disputed' if market.get('disputed') else
            'closed_pending_resolution' if raw.get('closed') else 'unresolved',
            'result': str(winners[0]['outcome']).lower() if resolved else None,
            'observed_at': receipt['received_at'], 'resolved_at': None,
            'time_role': 'first_observed_terminal_not_platform_resolution_time',
            'source': 'polymarket_clob_explicit_winner', 'receipt': receipt,
            'condition_id': market['condition_id'], 'winner_tokens': winners,
            'display_price_not_used_as_outcome': True}
