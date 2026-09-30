"""Compatibility entry point; maintain ForecastAgent/ultra_research_agent.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.ultra_research_agent", run_name="__main__")
else:
    from ForecastAgent import ultra_research_agent as _implementation
    sys.modules[__name__] = _implementation
