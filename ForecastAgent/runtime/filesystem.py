"""Platform path adaptation for durable local records, without changing identity."""
import os
from pathlib import Path


def io_path(path):
    """Use extended absolute Windows paths; leave POSIX paths unchanged."""
    path = Path(path)
    if os.name != 'nt':
        return path
    value = os.path.abspath(path)
    if value.startswith('\\\\?\\'):
        return Path(value)
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)
