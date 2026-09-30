"""Compatibility entry point; maintain ForecastAgent/retrieval_agent.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.retrieval_agent", run_name="__main__")
else:
    from ForecastAgent import retrieval_agent as _implementation
    sys.modules[__name__] = _implementation
