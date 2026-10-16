"""Run the pinned market engine with the shared forecast-input boundary."""
import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--monitor', required=True)
    parser.add_argument('--verification-only', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, os.getcwd())
    from ForecastAgent.market_repricing.runtime import activate
    activate(args.engine)
    os.environ['FORECAST_MODEL'] = 'nvidia/nemotron-3-super-120b-a12b:free'
    os.environ['FORECAST_MODEL_FALLBACK_SUPER'] = '0'
    from scoring_boundary import market_adapter
    from ForecastAgent.market_repricing.worker import run
    with market_adapter():
        print(json.dumps(run(args.root, args.monitor, verification_only=args.verification_only)))


if __name__ == '__main__':
    main()
