"""Durable reservations. Limits are program-owned, never provider/model inputs."""
from datetime import datetime, timezone

MAX_INITIAL_HTTP = 8
MAX_UPDATE_HTTP_TOTAL = 24
MAX_UPDATE_HTTP_DAILY = 3


def update_day():
    return datetime.now(timezone.utc).date().isoformat()


def reserve_update(bundle, attempt, save):
    attempts = bundle.setdefault('update_attempts', [])
    day = update_day()
    if len(attempts) >= MAX_UPDATE_HTTP_TOTAL or sum(a.get('budget_day') == day for a in attempts) >= MAX_UPDATE_HTTP_DAILY:
        raise ValueError('Update HTTP budget exhausted; three per UTC day and 24 lifetime attempts')
    attempt['budget_day'] = day
    return reserve(bundle, 'update_attempts', attempt, MAX_UPDATE_HTTP_TOTAL, save)


def reserve(bundle, field, attempt, maximum, save):
    if len(bundle[field]) >= maximum:
        raise ValueError(f"{field} budget exhausted; persists across restarts")
    bundle[field].append(attempt)
    save()  # Persist BEFORE invoking the provider; unknown outcomes consume quota.
    return attempt
