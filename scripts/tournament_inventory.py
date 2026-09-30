"""Compatibility entry point; maintain ForecastAgent/tournament_inventory.py instead."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.tournament_inventory", run_name="__main__")
else:
    from ForecastAgent import tournament_inventory as _implementation
    sys.modules[__name__] = _implementation
