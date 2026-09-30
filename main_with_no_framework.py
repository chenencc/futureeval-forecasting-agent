"""Compatibility entry point; maintain ForecastAgent/main_with_no_framework.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.main_with_no_framework", run_name="__main__")
else:
    from ForecastAgent import main_with_no_framework as _implementation
    sys.modules[__name__] = _implementation
