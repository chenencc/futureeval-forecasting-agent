"""Audit effective native allowance without rewriting physical or local journals."""
from contextlib import contextmanager
import copy
from pathlib import Path
from unittest.mock import patch

from .budgets import native_denials
from .common import now, write


@contextmanager
def native_accounting(folder):
    from ForecastAgent.intelligence import worker
    from ForecastAgent.releases import v1_0_5 as release
    original = worker.attempts
    native_handoff = release.raw_handoff_eligible
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
    def eligible(bundle):
        # Historical local denials did not transmit. They must not invalidate
        # the existing context-interruption raw-export gate. Other failures and
        # unknown reservations stay present and block that gate as before.
        denied = {(r['path'], r['sha256']) for r in native_denials(folder)}
        candidate = copy.deepcopy(bundle)
        kept, ignored = [], []
        for record in candidate.get('model_attempts', []):
            matched = any(path.replace(chr(92), '/').endswith('/collection/' + record['path']) and sha == record['sha256']
                          for path, sha in denied)
            (ignored if matched else kept).append(record)
        candidate['model_attempts'] = kept
        accepted = native_handoff(candidate)
        if ignored:
            write(Path(folder) / 'raw-handoff-budget-audit.json', {'at': now(),
                'excluded_non_http_receipts': ignored, 'eligible': bool(accepted),
                'original_result': bundle.get('result'), 'original_receipts_unchanged': True,
                'provider_reservations_unchanged': True, 'semantic_complete': False, 'budget_reset': False})
        return accepted
    with patch.object(worker, 'attempts', effective), patch.object(release, 'raw_handoff_eligible', eligible):
        yield
