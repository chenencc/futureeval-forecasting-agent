"""Compatibility entry point; maintain ForecastAgent/tavily_extract.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.tavily_extract", run_name="__main__")
else:
    from ForecastAgent import tavily_extract as _implementation
    sys.modules[__name__] = _implementation
