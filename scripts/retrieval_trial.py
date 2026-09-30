"""Compatibility entry point; maintain ForecastAgent/retrieval_trial.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.retrieval_trial", run_name="__main__")
else:
    from ForecastAgent import retrieval_trial as _implementation
    sys.modules[__name__] = _implementation
