"""Bind experimental resumes to the code that produced their derivatives."""
import hashlib
from pathlib import Path


def code_identity():
    root = Path(__file__).parent
    files = list(root.glob('*.py'))
    for folder in ('channels', 'tools/intelligence_box', 'research_loop'):
        files.extend(root.parent.joinpath(folder).glob('*.py'))
    files.append(root.parent / 'tools/capabilities.py')
    files.append(root.parent / 'tools/original_navigation.py')
    for name in ('competition/live.py','competition/platform.py','competition/queue.py',
                 'acquisition/pipeline.py','acquisition/recovery.py','releases/guard.py',
                 'releases/surfaces.py','releases/v1_0_5.py'):
        files.append(root.parent / name)
    return {str(p.relative_to(root.parent)).replace('\\', '/'):
            hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for p in sorted(files)}
