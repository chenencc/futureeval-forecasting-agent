"""Windows-safe callers for the byte-frozen analysis record implementation.

Only path transport changes. JSON encoding, atomic replacement and lifetime
reservation semantics remain those of the frozen analysis implementation.
"""
from ForecastAgent.analysis import pilot
from ForecastAgent.runtime.filesystem import io_path


def load(path):
    return pilot.load(io_path(path))


def save(path, value):
    return pilot.save(io_path(path), value)


class Journal(pilot.Journal):
    """Use the same physical reservation directory across ordinary/extended paths."""
    def __init__(self, root, limit):
        super().__init__(io_path(root), limit)

    def __call__(self, phase, record, token=None):
        return super().__call__(phase, record, io_path(token) if token is not None else None)
