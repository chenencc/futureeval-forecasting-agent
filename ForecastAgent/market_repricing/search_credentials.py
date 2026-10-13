"""Market-only Tavily failover composed outside physical worker accounting."""
import os
from pathlib import Path
import runpy


def configured():
    primary = os.environ.get('TAVILY_API_KEY', '')
    backup = os.environ.get('TAVILY_API_KEY2', '')
    return {'primary_secret': 'TAVILY_KEY3', 'backup_secret': 'TAVILY_KEY4',
            'primary_configured': bool(primary), 'backup_configured': bool(backup and backup != primary),
            'both_directions': True, 'exhaustion_http_statuses': [432, 433], 'budget_reset': False}


def transport(counted, root, cap_error):
    """Reuse the shared quota router; each transmitted fallback re-enters counted."""
    primary = os.environ.get('TAVILY_API_KEY', '')
    if not primary:
        return counted
    provider_file = Path(__file__).resolve().parents[1] / 'infrastructure/provider_failover.py'
    ProviderTransport = runpy.run_path(str(provider_file))['ProviderTransport']
    blocked = [None]
    def guarded(request, *args, **kwargs):
        try:
            return counted(request, *args, **kwargs)
        except cap_error as exc:
            blocked[0] = exc
            raise
    router = ProviderTransport(guarded, {'tavily': (
        primary, os.environ.get('TAVILY_API_KEY2', ''))}, Path(root) / 'provider-transport')
    def routed(request, *args, **kwargs):
        blocked[0] = None
        try:
            return router(request, *args, **kwargs)
        except Exception:
            # The generic transport sanitizes unknown exceptions. Keep local
            # capacity stops typed so the worker never retries an exhausted cap.
            if blocked[0] is not None:
                raise blocked[0]
            raise
    return routed
