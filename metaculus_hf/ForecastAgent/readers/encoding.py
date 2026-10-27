"""Bounded content decoding while original response bytes remain immutable."""
from io import BytesIO
import gzip
import zlib

MAX_DECODED_BYTES = 8_000_000


def decode_response(raw, headers=None, maximum=MAX_DECODED_BYTES):
    headers = {k.casefold():v for k,v in (headers or {}).items()}
    encoding = str(headers.get('content-encoding', '')).strip().casefold()
    if raw.startswith(b'\x1f\x8b'):
        encoding = 'gzip'
    if encoding in {'gzip', 'x-gzip'}:
        with gzip.GzipFile(fileobj=BytesIO(raw)) as stream:
            decoded = stream.read(maximum + 1)
    elif encoding == 'deflate':
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(raw, maximum + 1)
        if len(decoded) <= maximum and not decoder.eof:
            raise ValueError('Incomplete or unsupported deflate response')
    elif encoding in {'', 'identity'}:
        decoded = raw
        encoding = 'identity'
    else:
        raise ValueError('Unsupported content encoding: '+encoding)
    if len(decoded) > maximum:
        raise ValueError('Decoded response exceeds size limit')
    return decoded, encoding
