"""Compatibility entry point; maintain ForecastAgent/inspect_resolved_access.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.inspect_resolved_access", run_name="__main__")
else:
    from ForecastAgent import inspect_resolved_access as _implementation
    sys.modules[__name__] = _implementation
