"""Validate source identity and dependency imports without credentials or APIs."""
import ast,hashlib,importlib,json,re
from pathlib import Path

def check():
    root=Path(__file__).resolve().parents[1];lock=json.loads((root/'sources.lock.json').read_text())
    failures=[]
    for name,ref in lock['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file():failures.append(name);continue
        raw=path.read_bytes()
        # Python source/JSON newline conversion is the only allowed normalization.
        # Original upstream byte hashes remain in sha256 for provenance.
        expected=ref.get('normalized_sha256',ref['sha256'])
        actual=hashlib.sha256(raw.replace(b'\r\n',b'\n') if 'normalized_sha256' in ref else raw).hexdigest()
        if actual!=expected:failures.append(name)
    if failures:raise RuntimeError('SOURCE_LOCK_MISMATCH: '+','.join(failures))
    for path in root.rglob('*.py'):
        if path.relative_to(root).parts[0] in ('.venv','runtime'):continue
        ast.parse(path.read_bytes())
    for module in ['pipeline_v2.acquisition','pipeline_v2.review_recovery','ForecastAgent.competition.live','metaculus_agent.runner']:
        importlib.import_module(module)
    return dict(status='ok',pinned_files=len(lock['files']),credentials_used=False,provider_calls=0)
if __name__=='__main__':print(json.dumps(check()))
