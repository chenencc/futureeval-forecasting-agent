"""Compatibility entry point; maintain ForecastAgent/bot_helpers.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.bot_helpers", run_name="__main__")
else:
    from ForecastAgent import bot_helpers as _implementation
    sys.modules[__name__] = _implementation
