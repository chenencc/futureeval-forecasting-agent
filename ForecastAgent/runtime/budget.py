"""Durable reservations. Limits are program-owned, never provider/model inputs."""


def reserve(bundle, field, attempt, maximum, save):
    if len(bundle[field]) >= maximum:
        raise ValueError(f"{field} budget exhausted; persists across restarts")
    bundle[field].append(attempt)
    save()  # Persist BEFORE invoking the provider; unknown outcomes consume quota.
    return attempt
