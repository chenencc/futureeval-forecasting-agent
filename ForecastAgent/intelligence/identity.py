"""Bind experimental resumes to the code that produced their derivatives."""
import hashlib
from pathlib import Path


def code_identity():
    root = Path(__file__).parent
    files = list(root.glob('*.py'))
    for folder in ('channels', 'tools/intelligence_box'):
        files.extend(root.parent.joinpath(folder).glob('*.py'))
    files.append(root.parent / 'tools/capabilities.py')
    files.append(root.parent / 'tools/original_navigation.py')
    return {str(p.relative_to(root.parent)).replace('\\', '/'):
            hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for p in sorted(files)}
