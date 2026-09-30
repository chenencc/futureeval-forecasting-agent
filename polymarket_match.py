"""Compatibility entry point; maintain ForecastAgent/polymarket_match.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.polymarket_match", run_name="__main__")
else:
    from ForecastAgent import polymarket_match as _implementation
    sys.modules[__name__] = _implementation
