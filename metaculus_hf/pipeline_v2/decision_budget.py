"""Mercury full-request limits, independently enforced before reservation/HTTP.

OpenRouter model card checked 2026-10-22: 65,536 context tokens. The exact
Inception tokenizer is not bundled/published here. One serialized JSON UTF-8
byte is charged as one conservative input-token proxy, never chars/4. Count
requests' default ASCII-escaped wire representation, including state/questions,
and reserve 8,192 for provider formatting/internal overhead. This is a policy
proxy, NOT an official tokenizer count or a guarantee of provider acceptance.
"""
import json
from .contracts import PipelineError

POLICY='mercury-full-wire-byte-proxy-57344-v1'
MODEL_CONTEXT_TOKENS=65536
PROVIDER_RESERVE_TOKENS=8192
INPUT_TOKEN_PROXY_LIMIT=MODEL_CONTEXT_TOKENS-PROVIDER_RESERVE_TOKENS
HTTP_BYTE_LIMIT=64000

def wire_size(payload):
    try:return len(json.dumps(payload,allow_nan=False).encode('utf-8'))
    except (TypeError,ValueError,UnicodeError):raise PipelineError('DECISION_PAYLOAD_JSON') from None

def measure(payload):
    size=wire_size(payload)
    return dict(policy=POLICY,wire_bytes=size,input_token_proxy=size,exact_token_count=False,
        model_context_tokens=MODEL_CONTEXT_TOKENS,provider_reserve_tokens=PROVIDER_RESERVE_TOKENS,
        input_token_proxy_limit=INPUT_TOKEN_PROXY_LIMIT,http_byte_limit=HTTP_BYTE_LIMIT)

def guard(payload):
    receipt=measure(payload)
    if receipt['wire_bytes']>HTTP_BYTE_LIMIT:raise PipelineError('DECISION_CONTEXT_LIMIT','Full serialized request exceeds HTTP safety policy')
    if receipt['input_token_proxy']>INPUT_TOKEN_PROXY_LIMIT:raise PipelineError('DECISION_INPUT_TOKEN_BUDGET','Conservative full-request proxy exceeds reserved input budget')
    return receipt
