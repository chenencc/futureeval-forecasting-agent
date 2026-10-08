"""Read-only preparation command for the isolated tournament branch."""
import argparse
import json
from ForecastAgent.market_pulse.inventory import snapshot

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('command', choices=['snapshot'])
parser.add_argument('--root', required=True)
args = parser.parse_args()
report = snapshot(args.root)
print(json.dumps({key: value for key, value in report.items() if key != 'rows'}, indent=2))
