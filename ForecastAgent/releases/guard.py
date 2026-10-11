"""Bound a worker process and terminate its reader/browser descendants."""
import os
import signal
import subprocess
import time
from pathlib import Path


def execute(command, log, timeout, cwd=None):
    if not 0 < timeout <= 7200:
        raise ValueError('A positive bounded process deadline is required')
    log=Path(log);log.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    with log.open('wb') as output:
        proc=subprocess.Popen(command,cwd=cwd,stdout=output,stderr=subprocess.STDOUT,
            start_new_session=os.name!='nt',
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
                if os.name=='nt' else 0)
        timed_out=False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:proc.wait(timeout=2)
                except subprocess.TimeoutExpired:pass
                # Kill surviving browser children even if the leader exited.
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            proc.wait(timeout=10)
    return {'status':'timed_out' if timed_out else ('completed' if proc.returncode==0 else 'failed'),
            'returncode':proc.returncode,'pid':proc.pid,'elapsed_seconds':time.monotonic()-started,
            'timeout_seconds':timeout,'log':str(log),'process_tree_termination':timed_out}
