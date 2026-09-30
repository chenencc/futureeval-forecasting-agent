"""Compatibility entry point; maintain ForecastAgent/forecast_snapshots.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.forecast_snapshots", run_name="__main__")
else:
    from ForecastAgent import forecast_snapshots as _implementation
    sys.modules[__name__] = _implementation
