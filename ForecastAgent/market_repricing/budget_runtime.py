"""Audit effective native allowance without rewriting physical or local journals."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from .budgets import native_denials
from .common import now, write


@contextmanager
def native_accounting(folder):
    from ForecastAgent.intelligence import worker
    original = worker.attempts
    def effective(root):
        used = original(root)  # Validates every referenced receipt hash first.
        denied = native_denials(folder)
        excluded = len(denied)
        if excluded > used['http'] or excluded > used['failures']:
            raise ValueError('Native budget-denial accounting mismatch')
        result = used | {'http': used['http'] - excluded, 'failures': used['failures'] - excluded}
        write(Path(folder) / 'native-budget-accounting.json', {'at': now(),
            'original_usage': used, 'effective_usage': result, 'excluded_non_http_denials': denied,
            'original_receipts_unchanged': True, 'provider_reservations_unchanged': True, 'budget_reset': False})
        return result
    with patch.object(worker, 'attempts', effective):
        yield
