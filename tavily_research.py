"""Compatibility entry point; maintain ForecastAgent/tavily_research.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.tavily_research", run_name="__main__")
else:
    from ForecastAgent import tavily_research as _implementation
    sys.modules[__name__] = _implementation
