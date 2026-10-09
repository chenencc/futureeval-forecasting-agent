"""Bind experimental resumes to the code that produced their derivatives."""
import hashlib
from pathlib import Path


def code_identity():
    root = Path(__file__).parent
    return {p.name: hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for p in sorted(root.glob('*.py'))}
