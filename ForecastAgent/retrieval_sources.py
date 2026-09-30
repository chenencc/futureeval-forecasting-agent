"""Compatibility alias; maintain ForecastAgent/providers/financial.py."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.providers.financial", run_name="__main__")
else:
    from importlib import import_module
    sys.modules[__name__] = import_module("ForecastAgent.providers.financial")
