"""Compatibility alias; maintain ForecastAgent/evidence/snapshots.py."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("ForecastAgent.evidence.snapshots", run_name="__main__")
else:
    from importlib import import_module
    sys.modules[__name__] = import_module("ForecastAgent.evidence.snapshots")
