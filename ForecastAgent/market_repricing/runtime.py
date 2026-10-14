"""Load the latest selected development engine without a release fallback."""
import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys


def activate(engine):
    engine = Path(engine).resolve()
    from ForecastAgent.market_repricing import ENGINE_SHA
    sha = subprocess.check_output(['git', '-C', str(engine), 'rev-parse', 'HEAD'], text=True).strip()
    if sha != ENGINE_SHA:
        raise ValueError('Selected development commit mismatch; release fallback is forbidden')
    subprocess.run(['git', '-C', str(engine), 'diff', '--quiet', 'HEAD'], check=True)
    # The entire existing dependency tree comes from the development snapshot.
    import ForecastAgent
    from ForecastAgent import acquisition_shared
    ForecastAgent.__path__[:] = [str(engine / 'ForecastAgent')]
    for name in list(sys.modules):
        if name.startswith('ForecastAgent.') and not name.startswith(('ForecastAgent.market_repricing','ForecastAgent.acquisition_shared')):
            del sys.modules[name]
    from ForecastAgent.acquisition.pipeline import verify_baseline
    from ForecastAgent.intelligence.identity import code_identity
    verify_baseline()
    if not code_identity():
        raise ValueError('Development implementation identity unavailable')
    return sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--monitor', required=True)
    parser.add_argument('--verification-only', action='store_true')
    args = parser.parse_args()
    activate(args.engine)
    os.environ['FORECAST_MODEL'] = 'nvidia/nemotron-3-super-120b-a12b:free'
    os.environ['FORECAST_MODEL_FALLBACK_SUPER'] = '0'
    from .worker import run
    print(__import__('json').dumps(run(args.root, args.monitor, verification_only=args.verification_only)))


if __name__ == '__main__':
    main()
