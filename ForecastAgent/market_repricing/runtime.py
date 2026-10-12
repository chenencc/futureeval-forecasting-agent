"""Boot an additive research adapter on an exact immutable release checkout."""
import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys


def activate(release):
    release = Path(release).resolve()
    from ForecastAgent.market_repricing import RELEASE_SHA
    sha = subprocess.check_output(['git', '-C', str(release), 'rev-parse', 'HEAD'], text=True).strip()
    if sha != RELEASE_SHA:
        raise ValueError('Frozen release commit mismatch')
    # All frozen dependencies come from release; only this additive package is new.
    import ForecastAgent
    ForecastAgent.__path__[:] = [str(release / 'ForecastAgent')]
    for name in list(sys.modules):
        if name.startswith('ForecastAgent.') and not name.startswith('ForecastAgent.market_repricing'):
            del sys.modules[name]
    from ForecastAgent.releases.v1_0_5 import verify_release
    verify_release()
    return sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--monitor', required=True)
    parser.add_argument('--verification-only', action='store_true')
    args = parser.parse_args()
    activate(args.release)
    os.environ['FORECAST_MODEL'] = 'nvidia/nemotron-3-super-120b-a12b:free'
    os.environ['FORECAST_MODEL_FALLBACK_SUPER'] = '0'
    from .worker import run
    print(__import__('json').dumps(run(args.root, args.monitor, verification_only=args.verification_only)))


if __name__ == '__main__':
    main()
