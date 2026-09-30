"""Exclusive task locks with conservative recovery of dead/restored owners."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import uuid


def process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return True
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87
        try:
            code = ctypes.c_ulong()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def recoverable(path, owner):
    completed = set(filter(None, os.environ.get('FORECAST_COMPLETED_RESTORE_RUNS', '').split(',')))
    restored_root = os.environ.get('FORECAST_RESTORED_STATE_ROOT')
    trusted_restore = (os.environ.get('GITHUB_ACTIONS') == 'true' and completed and restored_root
                       and path.resolve().is_relative_to(Path(restored_root).resolve()))
    if trusted_restore and (not owner or owner.get('github_run_id') in completed):
        return True
    return bool(owner and owner.get('host') == socket.gethostname() and not process_alive(owner.get('pid')))


@contextmanager
def recovery_mutex(path):
    """OS-owned mutex releases on process death; the file may safely persist."""
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Task concurrent lock recovery is in progress') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def task_lock(directory):
    directory = Path(directory)
    lock = directory / '.running.lock'
    recovery = directory / '.lock.recovery'
    ident = str(uuid.uuid4())
    record = {'id': ident, 'pid': os.getpid(), 'host': socket.gethostname(),
              'github_run_id': os.environ.get('GITHUB_RUN_ID'), 'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
              'acquired_at': datetime.now(timezone.utc).isoformat()}
    with recovery_mutex(recovery):
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = json.loads(lock.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                owner = None
            if not isinstance(owner, dict):
                owner = None
            if not recoverable(lock, owner):
                raise RuntimeError('Task is already running or owner is unknown; concurrent recovery refused')
            lock.replace(directory / ('.abandoned-lock-' + ident + '.json'))
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(record, stream)
    try:
        yield record
    finally:
        try:
            if json.loads(lock.read_text(encoding='utf-8')).get('id') == ident:
                lock.unlink()
        except FileNotFoundError:
            pass
