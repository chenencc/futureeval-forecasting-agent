"""Book observations are optional context, never research admission gates."""
import copy
import math

from .common import instant


def admission_quote(market, dispatch_at, clock):
    """Retain the original observation without relabeling an old price as current."""
    quote = copy.deepcopy(market.get('last_quote') or {})
    stamp = quote.get('received_at')
    fresh = bool(stamp and instant(dispatch_at) <= instant(stamp) <= instant(clock))
    return {'observation': quote or None, 'at_admission': fresh,
            'status': quote.get('status', 'not_observed') if fresh else 'stale' if quote else 'not_observed',
            'research_admission_depends_on_book': False}


def forecast_context(quote, clock):
    """Freeze a research clock even when the price measurement is unavailable.

    Preserve status and timestamps verbatim. The context clock is explicitly
    distinct from the quote observation clock; no price is inferred from a
    one-sided, failed, stale or crossed book.
    """
    raw = copy.deepcopy(quote)
    context = copy.deepcopy(raw)
    observed_at = raw.get('received_at')
    fresh = bool(observed_at and 0 <= (instant(clock) - instant(observed_at)).total_seconds() <= 60)
    def valid(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    bid, ask = raw.get('yes_bid'), raw.get('yes_ask')
    paired = (fresh and raw.get('status') in {'usable', 'shallow_book', 'wide_spread'}
              and valid(bid) and valid(ask) and 0 <= bid <= ask <= 1
              and valid(raw.get('yes_bid_size')) and raw['yes_bid_size'] > 0
              and valid(raw.get('yes_ask_size')) and raw['yes_ask_size'] > 0)
    # Existing usable adapters can supply an already validated midpoint.
    midpoint = raw.get('midpoint') if fresh and raw.get('status') == 'usable' else None
    if not (valid(midpoint) and 0 <= midpoint <= 1):
        midpoint = (bid + ask) / 2 if paired else None
    flags = list(raw.get('quality_flags', []))
    if raw.get('status') != 'usable' and raw.get('status') not in flags:
        flags.append(raw.get('status', 'not_observed'))
    context.update(received_at=clock, received_at_role='analysis_context_frozen_at',
        quote_observed_at=observed_at, original_quote=raw, midpoint=midpoint,
        price_observation_status='observed' if midpoint is not None else 'unavailable',
        quality_flags=flags, book_required_for_research=False,
        baseline_available=midpoint is not None, midpoint_is_executable_price=False)
    return context
