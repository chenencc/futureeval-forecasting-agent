"""Explicit venue schemas; displayed prices never substitute for order books."""
import json
import re
from datetime import timedelta

from .common import digest, instant, number

GAMMA = 'https://gamma-api.polymarket.com/'
CLOB = 'https://clob.polymarket.com/'
KALSHI = 'https://external-api.kalshi.com/trade-api/v2/'
CRYPTO = re.compile(r'\b(crypto\w*|bitcoin|btc|ethereum|eth|solana|dogecoin|stablecoin\w*|blockchain|coinbase|binance|microstrategy|digital assets?|token price|nft\w*)\b', re.I)
GAMBLING = re.compile(r'\b(sports?|esports?|soccer|football|basketball|baseball|tennis|hockey|cricket|nba|nfl|nhl|mlb|fifa|ufc|champions league|premier league|rc lens|ol lyonnes|win the game|exact score|touchdowns?|passing yards?|counter.strike|cs2|league of legends|dota|valorant|mentions?|tweets?|followers?|likes?|retweets?|halftime|both teams to score)\b|\bvs\.?\s|\bKX(?:NFL|NBA|MLB|NHL|SOCCER|ESPORTS|CS2|LOL)', re.I)
PRICE_ONLY = re.compile(r'\b(price|trading|trade|close|closing|above|below|hit|reach)\b.{0,65}\b(\$|usd|stock|share price|gold|silver|oil|nasdaq|s&p|dow|dollar)', re.I)
DOMAINS = {
    'macro_policy': r'\b(fed|fomc|interest rate|inflation|cpi|gdp|unemployment|payroll|central bank|ecb|monetary|tariff)\b',
    'company_event': r'\b(earnings|revenue|eps|merger|acquisition|antitrust|product launch|fda approval|deliveries)\b',
    'government_law': r'\b(court|lawsuit|ruling|bill|legislation|congress|senate|parliament|ceasefire|treaty|sanction|vote|hearing|government|budget)\b',
    'election': r'\b(election|president|governor|polling|nominee|referendum|debate|midterms)\b',
    'science_health': r'\b(nasa|spacex|launch|spacecraft|clinical trial|outbreak|vaccine|public health|research|ai model|artificial intelligence)\b'
}


def array(value):
    return json.loads(value) if isinstance(value, str) else value


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
                'created_at': raw.get('createdAt'), 'close_at': raw.get('endDate'),
                'close_time_role': 'platform_hint_not_authoritative_event_deadline',
                'binary': binary, 'yes_token': str(tokens[index]) if binary else None,
                'composite': raw.get('comboStatus') not in {None, '', 'disabled'} or bool(re.search(r'\band\b', str(raw.get('question', '')), re.I)),
                'tradable': bool(raw.get('active') and not raw.get('closed') and not raw.get('archived') and raw.get('enableOrderBook') and raw.get('acceptingOrders')),
                'disputed': 'disputed' in str(raw.get('umaResolutionStatuses', '')).lower(),
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
            'binary': raw.get('market_type') == 'binary', 'yes_token': None,
            'composite': bool(raw.get('mve_collection_ticker')) or bool(re.search(r'\band\b', str(raw.get('title', '')), re.I)),
            'tradable': raw.get('status') in {'open', 'active'}, 'disputed': False,
            'terminal_result': raw.get('result') if raw.get('result') in {'yes', 'no'} else None,
            'discovery_liquidity_hint': raw.get('open_interest_fp') or 0,
            'discovery_volume_hint': raw.get('volume_fp') or 0,
            'screen_text': ' '.join(str(raw.get(k) or '') for k in ('ticker', 'event_ticker', 'title', 'subtitle', 'yes_sub_title', 'rules_primary', 'rules_secondary', 'category'))}


def screen(market, clock, cfg):
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
    if not market['rules'].strip():
        return {'status': 'waiting_rules', 'reason': 'exact_rules_missing'}
    if market['disputed']:
        return {'status': 'waiting_market', 'reason': 'disputed_rules'}
    domain = next((k for k, expression in DOMAINS.items() if re.search(expression, text, re.I)), None)
    if not domain:
        return {'status': 'waiting_relevance', 'reason': 'event_catalyst_not_identified'}
    # This is a source lead, not a claim that a catalyst date is confirmed.
    result = {'domain': domain, 'catalyst_status': 'plausible_unconfirmed',
              'catalyst_evidence': [], 'close_time_role': market['close_time_role']}
    try:
        remaining = (instant(market['close_at']) - instant(clock)).total_seconds() / 3600
    except (TypeError, ValueError):
        return {**result, 'status': 'waiting_rules', 'reason': 'close_hint_missing'}
    if remaining < cfg['minimum_close_buffer_hours']:
        return {**result, 'status': 'short_resolution_cohort', 'reason': 'insufficient_repricing_window'}
    if remaining > cfg['maximum_close_horizon_days'] * 24:
        return {**result, 'status': 'waiting_catalyst', 'reason': 'distant_event_outside_initial_pilot_window'}
    if not market['tradable']:
        return {**result, 'status': 'waiting_market', 'reason': 'not_orderable'}
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
    status = 'usable'
    if spread < 0:
        status = 'crossed_book'
    elif spread > cfg['maximum_spread'] + 1e-9:
        status = 'wide_spread'
    elif min(bid_size, ask_size) < cfg['minimum_top_size']:
        status = 'shallow_book'
    return {**metadata, 'status': status, 'yes_bid': bid, 'yes_ask': ask,
            'yes_bid_size': bid_size, 'yes_ask_size': ask_size, 'spread': spread,
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
    return value, receipt
