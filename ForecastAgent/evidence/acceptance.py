"""Mechanical acquisition checks only; no truth, probability or quality score."""
import base64
import hashlib
import json
from ForecastAgent.readers.saved import select, version_digest
from ForecastAgent.tavily_research import canonical_url


def collection_acceptance(bundle):
    failures = []; warnings = []; raw_checked = 0
    pages = bundle.get('pages', {})
    source_urls = set(bundle.get('source_leads', {}))
    source_urls.update(canonical_url(hit['url']) for search in bundle.get('searches', []) for hit in search.get('results', []))
    unread = sorted(source_urls - set(pages))
    versions = bundle.get('page_history', {})
    for url, snapshots in [(u, [p]) for u, p in pages.items()] + [(u, ps) for u, ps in versions.items()]:
        for page in snapshots:
            if not page.get('raw_response_base64') or not page.get('sha256'):
                warnings.append({'url': url, 'issue': 'Missing original raw response/hash'})
                continue
            try:
                raw = base64.b64decode(page['raw_response_base64'], validate=True)
                if hashlib.sha256(raw).hexdigest() != page['sha256']:
                    failures.append({'url': url, 'issue': 'Raw response hash mismatch'})
                raw_checked += 1
            except (ValueError, TypeError):
                failures.append({'url': url, 'issue': 'Invalid raw response encoding'})
            if page.get('content_truncated') or page.get('documents_truncated'):
                warnings.append({'url': url, 'issue': 'Parsed source is truncated'})
    for excerpt in bundle.get('excerpts', []):
        try:
            candidates = [pages.get(excerpt['url'], {})] + versions.get(excerpt['url'], [])
            source = next(p for p in candidates if p.get('sha256') == excerpt.get('source_sha256')
                          and (not excerpt.get('source_parsed_sha256') or version_digest(p) == excerpt['source_parsed_sha256']))
            _, text, _ = select({excerpt['url']: source}, excerpt['url'], excerpt['location'].get('document_index'))
            if text[excerpt['start_char']:excerpt['end_char']] != excerpt['text']:
                failures.append({'excerpt': excerpt['id'], 'issue': 'Excerpt coordinates differ from saved version'})
        except (KeyError, StopIteration, ValueError, TypeError):
            failures.append({'excerpt': excerpt.get('id'), 'issue': 'Excerpt source version is unavailable'})
    for market in bundle.get('market_snapshots', {}).values():
        snapshot = market.get('snapshot', {})
        raw = snapshot.get('raw_response')
        if raw is not None:
            digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest != snapshot.get('raw_response_sha256'):
                failures.append({'market': market.get('id'), 'issue': 'Market response hash mismatch'})
        else:
            warnings.append({'market': market.get('id'), 'issue': 'Missing raw market response'})
    searches = bundle.get('searches', [])
    attempts = bundle.get('fetch_attempts', [])
    if len(searches) > 3 or len(attempts) > 8 or len(bundle.get('extract_attempts', [])) > 1:
        failures.append({'issue': 'Attempt budget exceeded'})
    failed_reads = [a for a in attempts if a.get('status') in {'failed', 'reserved'}]
    failed_searches = [a for a in searches if a.get('status') in {'failed', 'reserved'}]
    unassociated = [n['id'] for n in bundle.get('plan') or []
                    if not any(n['id'] in e.get('need_ids', []) for e in bundle.get('excerpts', []))
                    and not any(n['id'] in a.get('need_ids', []) and a.get('status') == 'completed' for a in attempts)]
    warnings.extend({'issue': 'Acquisition need has no associated material', 'need_id': n} for n in unassociated)
    if failed_reads:
        warnings.append({'issue': 'Failed/interrupted reads remain', 'count': len(failed_reads)})
    if failed_searches:
        warnings.append({'issue': 'Failed/interrupted searches remain', 'count': len(failed_searches)})
    if not pages and not bundle.get('market_snapshots'):
        warnings.append({'issue': 'No source bodies or market responses were captured'})
    if bundle.get('quarantine'):
        warnings.append({'issue': 'Temporally quarantined material remains', 'count': len(bundle['quarantine'])})
    if unread:
        warnings.append({'issue': 'Discovered sources remain unread', 'count': len(unread)})
    status = 'failed' if failures else 'accepted_with_gaps' if warnings else 'accepted'
    return {'schema': 'collection_acceptance_v1', 'status': status, 'truth_verified': False,
            'raw_versions_checked': raw_checked, 'page_count': len(pages), 'market_snapshot_count': len(bundle.get('market_snapshots', {})),
            'excerpt_count': len(bundle.get('excerpts', [])), 'failures': failures, 'warnings': warnings,
            'source_count': len(source_urls), 'unread_source_count': len(unread), 'unread_urls': unread[:120],
            'resources': {'tavily_basic_attempts': len(searches), 'free_http_attempts': len(attempts)},
            'scope': 'Capture integrity and acquisition gaps only; not factual correctness or exhaustive coverage.'}
