"""Compatibility entry point; maintain ForecastAgent/historical_research.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.historical_research", run_name="__main__")
else:
    from ForecastAgent import historical_research as _implementation
    sys.modules[__name__] = _implementation
