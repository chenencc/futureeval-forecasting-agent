"""Explicit tournament routing for independent production ledgers."""
import os

ALLOWED = frozenset({'fall-futureeval-2026', 'minibench'})


def configured_tournament():
    value = os.environ.get('FORECAST_TOURNAMENT', 'fall-futureeval-2026')
    if value not in ALLOWED:
        raise ValueError('Unapproved production tournament')
    return value
