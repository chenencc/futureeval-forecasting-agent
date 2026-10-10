"""CLI for offline catalog inspection and explicitly bounded public acquisition."""
import argparse
import json
from .catalog import catalog
from .core import Toolbox

parser=argparse.ArgumentParser()
parser.add_argument("action",choices=["catalog","fetch"])
parser.add_argument("--source")
parser.add_argument("--parameters",default="{}")
parser.add_argument("--root")
parser.add_argument("--max-requests",type=int,default=30)
args=parser.parse_args()
if args.action=="catalog":
    print(json.dumps(catalog(),indent=2))
else:
    if not args.root or not args.source: parser.error("fetch needs --root and --source")
    box=Toolbox(args.root,max_requests=args.max_requests)
    try: print(json.dumps(box.fetch(args.source,json.loads(args.parameters)),ensure_ascii=False,indent=2))
    finally: box.close()
