"""Compatibility entry point; maintain ForecastAgent/main.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.main", run_name="__main__")
else:
    from ForecastAgent import main as _implementation
    sys.modules[__name__] = _implementation
