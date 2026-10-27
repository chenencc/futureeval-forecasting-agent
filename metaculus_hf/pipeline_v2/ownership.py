"""OS-owned execution guards and termination limited to our own process trees."""
import multiprocessing as mp
import os,signal,subprocess,sys
from pathlib import Path
from .contracts import digest
from .repository import ensure


def guard(root,kind,qid,rid):return Path(root)/'.locks'/(kind+'-'+digest([qid,rid])+'.lock')


def own_session():
    if os.name=='posix' and mp.parent_process() is not None and os.getpgrp()!=os.getpid():os.setsid()


def stop_tree(process):
    if not process.is_alive():process.join(.1);return
    if os.name=='posix' and os.getpgid(process.pid)==process.pid:
        os.killpg(process.pid,signal.SIGTERM);process.join(1)
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    elif os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    else:process.terminate()
    process.join(2)
    if process.is_alive():process.kill();process.join(1)
    ensure(not process.is_alive(),'OWNED_PROCESS_TREE_STOP_FAILED')


def parent_death_signal():
    """Linux kernel cascade: orphan workers cannot keep a recorder slot alive."""
    parent=mp.parent_process()
    if not sys.platform.startswith('linux') or parent is None:return
    import ctypes
    expected=parent.pid
    if os.getppid()!=expected:os._exit(125)
    libc=ctypes.CDLL(None,use_errno=True)
    ensure(libc.prctl(1,int(signal.SIGTERM),0,0,0)==0,'PARENT_DEATH_SIGNAL_SETUP_FAILED')
    if os.getppid()!=expected:os._exit(125)


from contextlib import contextmanager
@contextmanager
def capture_alarm(seconds):
    """Linux task-local backstop; cancelled before the independent book wait."""
    active=sys.platform.startswith('linux')
    if active:
        previous=signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM,lambda *_:os._exit(124));signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:
        if active:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
