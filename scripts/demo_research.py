"""Compatibility entry point; maintain ForecastAgent/demo_research.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.demo_research", run_name="__main__")
else:
    from ForecastAgent import demo_research as _implementation
    sys.modules[__name__] = _implementation
