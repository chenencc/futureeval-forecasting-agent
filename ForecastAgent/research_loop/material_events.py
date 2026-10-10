"""One auditable material-arrival stream across tools and external stages."""
import hashlib
from datetime import datetime, timezone

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.readers.quality import body_diagnostics


def observe(task, origin):
    if task.bundle.get('request', {}).get('capability_policy') != 'native_channels_v1':
        return
    journal = task.bundle['channel_tools'].setdefault('material_events', {'events': [], 'seen': {}})
    previous = None
    reconstructed = {}
    for index, event in enumerate(journal['events'], 1):
        if event.get('previous_sha256') != previous or digest({k:v for k,v in event.items() if k != 'sha256'}) != event['sha256']:
            raise ValueError('Material arrival journal checksum mismatch')
        if event['index'] != index or event.get('previous_source_version') != reconstructed.get(event['url']):
            raise ValueError('Material arrival sequence mismatch')
        reconstructed[event['url']] = event['source_version']
        previous = event['sha256']
    if reconstructed != journal['seen']:
        raise ValueError('Material arrival cache differs from its journal')
    readable_arrival = False
    for url, page in sorted(task.bundle.get('pages', {}).items()):
        text = page.get('content', '')
        body_hash = hashlib.sha256(text.encode()).hexdigest()
        if page.get('content_sha256') and page['content_sha256'] != body_hash:
            raise ValueError('Arriving material body checksum mismatch')
        version = digest({'body': body_hash, 'raw': page.get('sha256')})
        if journal['seen'].get(url) == version:
            continue
        readable = body_diagnostics(text)['usable_text'] and page.get('body_diagnostics', {}).get('usable_text') is not False
        event = {'index': len(journal['events']) + 1, 'url': url, 'body_sha256': body_hash,
            'raw_sha256': page.get('sha256'), 'source_version': version,
            'previous_source_version': journal['seen'].get(url), 'capture_time': page.get('retrieved_at_utc'),
            'origin': origin, 'capture_method': page.get('capture_method'),
            'parsed_view_method': page.get('parsed_view_method'), 'readable': readable,
            'truth_verified': False, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
            'previous_sha256': previous}
        event['sha256'] = digest(event)
        journal['events'].append(event); journal['seen'][url] = version; previous = event['sha256']
        readable_arrival |= readable
    if readable_arrival:
        from ForecastAgent.research_loop import fusion
        if fusion.enabled(task):
            fusion.initialize(task)['pending_map_update'] = True
    return journal
