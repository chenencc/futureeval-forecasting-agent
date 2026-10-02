"""Operate the shadow queue without a forecast submission endpoint."""
import argparse
import json
import os
from pathlib import Path

from ForecastAgent.runtime.task_lock import task_lock
from .queue import Queue
from .worker import drain

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', required=True)
parser.add_argument('--snapshot', help='Directory containing monitor index.json and raw posts')
parser.add_argument('--collector-root', help='Existing collection-worker state directory')
parser.add_argument('--execute', action='store_true', help='Run supplement and frozen analysis in shadow mode')
parser.add_argument('--network-supplement', action='store_true')
parser.add_argument('--limit', type=int, default=5)
args = parser.parse_args()
Path(args.root).mkdir(parents=True, exist_ok=True)
if args.snapshot:
    with task_lock(Path(args.root)):
        Queue(args.root).ingest(args.snapshot)
if args.collector_root:
    report = drain(args.root, args.collector_root, execute=args.execute,
                   token=os.environ.get('METACULUS_TOKEN', ''), limit=args.limit,
                   network_supplement=args.network_supplement)
else:
    if args.execute:
        parser.error('--execute requires --collector-root')
    with task_lock(Path(args.root)):
        report = Queue(args.root).report()
print(json.dumps(report, indent=2))
