"""Versioned source-bound acquisition capabilities; no provider ledger."""
import hashlib
from pathlib import Path

VERSION = 'shared-acquisition-p0-v1'


def identity():
    root=Path(__file__).parent
    return {'version':VERSION,'network_requests':0,'host_budgets_required':True,
        'files_sha256_lf':{p.name:hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
            for p in sorted(root.glob('*.py'))}}
