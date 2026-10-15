"""Run one local mirror inside the scheduler-owned process, without a shell.

Install with the base interpreter's pythonw.exe, not a virtualenv redirector.
This avoids detached PowerShell/Python children surviving scheduler timeouts.
"""
import argparse
from contextlib import redirect_stderr,redirect_stdout
from datetime import datetime
import importlib
import os
from pathlib import Path
import sys
import traceback


def run(mode,root,gh):
    root=Path(root)
    repo=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(repo))
    os.chdir(repo)
    os.environ.update(FORECAST_GH_PATH=str(gh),GH_PROMPT_DISABLED='1',GH_NO_UPDATE_NOTIFIER='1')
    logs=root/'logs' if mode=='archive' else root
    logs.mkdir(parents=True,exist_ok=True)
    path=logs/'sync.log'
    if path.exists() and path.stat().st_size>10*1024**2:
        path.rename(logs/('sync-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.log'))
    with path.open('a',encoding='utf-8',buffering=1) as output,redirect_stdout(output),redirect_stderr(output):
        print(datetime.now().astimezone().isoformat(),mode,'sync started',flush=True)
        previous=sys.argv
        module='ForecastAgent.local_sync' if mode=='archive' else 'ForecastAgent.market_store.cli'
        sys.argv=[module,'sync','--root',str(root)]
        code=0
        try:
            importlib.import_module(module).main()
        except SystemExit as exc:
            code=exc.code if isinstance(exc.code,int) else 1
        except Exception:
            traceback.print_exc();code=1
        finally:
            sys.argv=previous
        print(datetime.now().astimezone().isoformat(),mode,'sync exit',code,flush=True)
        return code


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('archive','databases'))
    parser.add_argument('--root',required=True)
    parser.add_argument('--gh-path',required=True)
    args=parser.parse_args()
    return run(args.mode,args.root,args.gh_path)


if __name__=='__main__':raise SystemExit(main())
