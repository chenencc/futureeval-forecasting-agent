"""Model-independent acquisition interface with a configurable OpenRouter backend."""
import os

ULTRA_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'
SUPER_MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'
DEFAULT_MODEL = ULTRA_MODEL
_route = None


class ModelRoute:
    """Two consecutive service failures select Super for the rest of a batch."""
    def __init__(self):
        self.failures = 0
        self.fallback = False
        self.reason = None

    def model(self):
        return SUPER_MODEL if self.fallback else ULTRA_MODEL

    def observe(self, record):
        response = record.get('response')
        response_error = response.get('error') if isinstance(response,dict) else None
        response_error = response_error if isinstance(response_error,dict) else {}
        code = record.get('http_status') or response_error.get('code')
        status = record.get('status')
        if status == 'received':
            self.failures = 0
            return False
        # Shared account limits and invalid requests cannot be repaired by switching models.
        if code in {400,401,402,403,429}:
            return False
        retryable = (code == 404 or isinstance(code,int) and code >= 500 or
                     status in {'transport_error','missing_choices'})
        if retryable and not self.fallback:
            self.failures += 1
            if self.failures >= 2:
                self.fallback = True
                self.reason = 'Two consecutive Ultra service failures; preserve all task budgets.'
        return retryable


def reset_route():
    global _route
    _route = None


def configured_model():
    return os.environ.get('FORECAST_MODEL', '').strip() or DEFAULT_MODEL


def ask_model(*args, **kwargs):
    # Keep the existing audited HTTP implementation and legacy import seams.
    from ForecastAgent.providers.ultra import ask_ultra
    global _route
    if os.environ.get('FORECAST_MODEL_FALLBACK_SUPER') == '1' and configured_model() == ULTRA_MODEL:
        if _route is None:
            _route = ModelRoute()
        kwargs['model_route'] = _route
    return ask_ultra(*args, **kwargs)
