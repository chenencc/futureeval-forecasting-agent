"""Optional pinned local tokenizer; estimates are not hosted prompt accounting."""
import hashlib,os
from functools import lru_cache
from pathlib import Path

MODEL='baselabs/mammoth-1'
REVISION='61cec44c281c693992e3d6ebeb44dd3402dfa186'
SHA256='a5cd9732badce41de57e6efce8302930ded1c1188c5f81feb2bd6c24c4a1941f'
RESERVE=4096  # Remote serialization/template and per-question branch headroom.

@lru_cache(maxsize=2)
def _load(path):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SHA256:raise ValueError('DECISION_TOKENIZER_HASH_MISMATCH')
    from tokenizers import Tokenizer
    return Tokenizer.from_str(raw.decode('utf-8'))

def available():
    # The archived tokenizer is Mammoth-specific, never Mercury token accounting.
    from .analysis_policy import DECISION_MODEL
    if os.environ.get('METACULUS_DECISION_MODEL',DECISION_MODEL)!='baseten/mammoth-1:free':return False
    path=os.environ.get('METACULUS_DECISION_TOKENIZER_PATH','')
    if not path:return False
    _load(str(Path(path).resolve()))
    return True

@lru_cache(maxsize=512)
def _count(path,text):return len(_load(path).encode(text,add_special_tokens=False).ids)

def count(payload):
    if not available():return None
    from .decision_model import encode
    return _count(str(Path(os.environ['METACULUS_DECISION_TOKENIZER_PATH']).resolve()),encode(payload).decode())

def policy(context_length):
    from .decision_profile import experimental
    if not experimental() or not available():return {}
    usable=context_length-RESERVE
    if usable<2048:raise ValueError('DECISION_TOKEN_HEADROOM_TOO_SMALL')
    return dict(token_policy='pinned-local-json-estimate/1',tokenizer_model=MODEL,tokenizer_revision=REVISION,
        tokenizer_sha256=SHA256,template_token_reserve=RESERVE,
        first_token_limit=min(6000,usable),second_token_limit=min(9000,usable))
