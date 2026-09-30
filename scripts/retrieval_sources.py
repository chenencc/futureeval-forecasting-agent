"""Compatibility entry point; maintain ForecastAgent/retrieval_sources.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.retrieval_sources", run_name="__main__")
else:
    from ForecastAgent import retrieval_sources as _implementation
    sys.modules[__name__] = _implementation
