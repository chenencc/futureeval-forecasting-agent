"""Model-independent acquisition interface with a configurable OpenRouter backend."""
import os

DEFAULT_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'


def configured_model():
    return os.environ.get('FORECAST_MODEL', '').strip() or DEFAULT_MODEL


def ask_model(*args, **kwargs):
    # Keep the existing audited HTTP implementation and legacy import seams.
    from ForecastAgent.providers.ultra import ask_ultra
    return ask_ultra(*args, **kwargs)
