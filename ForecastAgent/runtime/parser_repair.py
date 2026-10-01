"""Reparse preserved compressed bodies; no requests, changed cutoffs or quota grants."""
import base64
import hashlib
from ForecastAgent.readers.loader import load_response
from ForecastAgent.readers.saved import version_digest


def repair_compressed_pages(task):
    repaired = []
    for url, old in list(task.bundle['pages'].items()):
        if old.get('parser_version') == 'document_reader_v4' or not old.get('raw_response_base64'):
            continue
        raw = base64.b64decode(old['raw_response_base64'], validate=True)
        if not raw.startswith(b'\x1f\x8b'):
            continue
        if hashlib.sha256(raw).hexdigest() != old.get('sha256'):
            raise ValueError('Preserved compressed response failed its raw hash check')
        parsed = load_response({'url':old.get('url',url), 'final_url':old.get('final_url',url),
            'raw':raw, 'content_type':old.get('declared_content_type',old['content_type']),
            'charset':old.get('charset','utf-8'), 'response_headers':old.get('response_headers',{})},
            retrieved_at=old['retrieved_at_utc'])
        updated = {**old, **parsed}
        # An archived replay remains an archive; parsing never renews capture time.
        for key in ('url','capture_method','archive_timestamp','archive_provenance','temporal_status'):
            if key in old:
                updated[key] = old[key]
        if old.get('capture_method') == 'wayback_replay':
            updated['links'] = []
        task.store_page(url, updated)
        record = {'url':url, 'raw_sha256':old['sha256'],
            'old_parsed_sha256':version_digest(old), 'new_parsed_sha256':version_digest(updated),
            'old_chars':len(old['content']), 'new_chars':len(updated['content']),
            'parser_version':updated['parser_version'], 'http_attempts':0,
            'capture_time_unchanged':old['retrieved_at_utc']==updated['retrieved_at_utc']}
        task.bundle.setdefault('parser_repairs', []).append(record)
        repaired.append(url)
    if repaired:
        task.bundle['passages'] = {}
        task.bundle.setdefault('control', {})['repaired_source_urls'] = repaired
        task.bundle['control']['repair_read_done'] = False
        task.bundle['control']['repair_banked'] = False
        task.save()
    return repaired
