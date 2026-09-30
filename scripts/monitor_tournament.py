"""Compatibility entry point; maintain ForecastAgent/monitor_tournament.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.monitor_tournament", run_name="__main__")
else:
    from ForecastAgent import monitor_tournament as _implementation
    sys.modules[__name__] = _implementation
